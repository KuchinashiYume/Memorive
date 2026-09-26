"""Versioned source presets. Credentials stay in the environment, never in state."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from .contracts import canonical_sha256

PRESETS = {
    'arxiv': ('arXiv', 'https://export.arxiv.org/api/query', ''),
    'crossref': ('Crossref', 'https://api.crossref.org/works', ''),
    'web-of-science': ('Web of Science Starter', 'https://api.clarivate.com/apis/wos-starter/v1/documents', 'WOS_API_KEY'),
}
CATALOG = tuple((p, v[0], '官方文献元数据', 'CONNECTOR_BUILT', 'pr_os_research_runtime.transport')
                for p,v in PRESETS.items())
SELECTABLE = frozenset(PRESETS)
SLOT_IDS = ('literature-1','literature-2','literature-3')
FIELDS = {'slot_id','provider','display_name','endpoint','credential_env','enabled'}

def configured_slot(slot_id, provider, enabled=False):
    name, endpoint, env = PRESETS.get(provider, ('','',''))
    return dict(slot_id=slot_id, provider=provider, display_name=name, endpoint=endpoint,
                credential_env=env, enabled=enabled)

def _validate_slot(row, index):
    if (not isinstance(row,dict) or set(row)!=FIELDS or row['slot_id']!=SLOT_IDS[index]
        or row['provider'] not in SELECTABLE | {None} or type(row['enabled']) is not bool):
        raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
    if row['provider'] is None:
        if row != configured_slot(row['slot_id'],None):
            raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
        return
    if (not isinstance(row['display_name'],str) or not row['display_name'].strip()
        or any(ord(c)<32 for c in row['display_name'])
        or row['endpoint']!=PRESETS[row['provider']][1]):
        raise ValueError('LITERATURE_SOURCE_ENDPOINT_UNSUPPORTED')
    env=row['credential_env']
    if (not isinstance(env,str) or (env and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',env))
        or (row['provider']!='web-of-science' and env)):
        raise ValueError('LITERATURE_CREDENTIAL_VARIABLE_INVALID')

def _selected(slots):
    return sorted(row['provider'] for row in slots if row['provider'] and row['enabled'])

def load(root: Path):
    path=root/'literature_sources.json'
    if not path.exists():
        slots=[configured_slot(s,p,p!='web-of-science') for s,p in zip(SLOT_IDS,PRESETS)]
        return dict(schema_version='LiteratureSourceSelection-v3', revision=0, selected=_selected(slots), slots=slots)
    value=json.loads(path.read_text(encoding='utf8'))
    digest=value.pop('sha256',None)
    if digest!=canonical_sha256(value) or type(value.get('revision')) is not int or value['revision']<0:
        raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
    version=value.get('schema_version')
    if version=='LiteratureSourceSelection-v3':
        if set(value)!={'schema_version','revision','selected','slots'} or len(value['slots'])!=3:
            raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
        for i,row in enumerate(value['slots']):_validate_slot(row,i)
        providers=[r['provider'] for r in value['slots'] if r['provider']]
        if len(set(providers))!=len(providers) or value['selected']!=_selected(value['slots']):
            raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
        return value
    # Read-only migration preserves the user's enabled choices and revision.
    expected={'schema_version','revision','selected'} | ({'slots'} if version=='LiteratureSourceSelection-v2' else set())
    if (version not in {'LiteratureSourceSelection-v1','LiteratureSourceSelection-v2'} or set(value)!=expected
        or not isinstance(value['selected'],list) or any(p not in SELECTABLE for p in value['selected'])
        or len(set(value['selected']))!=len(value['selected'])):
        raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
    legacy=value.get('slots',[dict(slot_id=s,provider=value['selected'][i] if i<len(value['selected']) else None) for i,s in enumerate(SLOT_IDS)])
    if (len(legacy)!=3 or any(set(r)!={'slot_id','provider'} or r['slot_id']!=SLOT_IDS[i]
        or r['provider'] not in SELECTABLE | {None} for i,r in enumerate(legacy))
        or sorted(r['provider'] for r in legacy if r['provider'])!=sorted(value['selected'])):
        raise ValueError('LITERATURE_SOURCE_SETTINGS_INVALID')
    slots=[configured_slot(r['slot_id'],r['provider'],bool(r['provider'])) for r in legacy]
    unused=[p for p in PRESETS if p not in value['selected']]
    for row in slots:
        if row['provider'] is None and unused:
            row.update(configured_slot(row['slot_id'],unused.pop(0),False))
    return dict(schema_version='LiteratureSourceSelection-v3',revision=value['revision'],selected=_selected(slots),slots=slots)

def projection(root: Path):
    state=load(root)
    slots=[]
    for saved in state['slots']:
        row=dict(saved)
        ready=not saved['credential_env'] or bool(os.environ.get(saved['credential_env'],'').strip())
        row.update(credential_configured=ready,
            configuration_status='CREDENTIAL_REQUIRED' if not ready else 'READY' if saved['provider'] else 'NOT_CONFIGURED')
        slots.append(row)
    selected=sorted(r['provider'] for r in slots if r['provider'] and r['enabled'] and r['credential_configured'])
    return dict(state, slots=slots, selected=selected, execution_ready=bool(selected),
        execution_status='METADATA_READY' if selected else 'SELECT_SOURCE_REQUIRED',
        sources=[dict(source_id=p,display_name=name,purpose=purpose,implementation_status=status,
            selected=p in selected,selectable=True,engine=engine) for p,name,purpose,status,engine in CATALOG])

def save(owner, source, expected_revision):
    if not isinstance(source,dict):raise ValueError('LITERATURE_SOURCE_SELECTION_INVALID')
    with owner._write_lock():
        state=load(owner.root)
        if type(expected_revision) is not int or expected_revision!=state['revision']:
            raise ValueError('LITERATURE_SOURCE_REVISION_CONFLICT')
        slots=state['slots']
        if set(source)=={'source_id','selected'}:
            provider=source['source_id']
            if provider not in SELECTABLE or type(source['selected']) is not bool:
                raise ValueError('LITERATURE_SOURCE_SELECTION_INVALID')
            row=next((r for r in slots if r['provider']==provider),None)
            if row is None and source['selected']:
                row=next((r for r in slots if r['provider'] is None),None)
                if row is None:raise ValueError('LITERATURE_SLOTS_FULL')
                row.update(configured_slot(row['slot_id'],provider))
            if row is not None:row['enabled']=source['selected']
        elif set(source) in ({'slot_id','provider'},FIELDS):
            if source['slot_id'] not in SLOT_IDS:raise ValueError('LITERATURE_SOURCE_SELECTION_INVALID')
            i=SLOT_IDS.index(source['slot_id'])
            row=(configured_slot(source['slot_id'],source['provider'],bool(source['provider']))
                 if set(source)=={'slot_id','provider'} else dict(source))
            _validate_slot(row,i)
            # Moving a built-in preset between slots swaps the existing slot,
            # retaining source preferences without duplicate providers.
            other=next((r for r in slots if r['slot_id']!=row['slot_id'] and r['provider'] and r['provider']==row['provider']),None)
            if other is not None:
                previous=dict(slots[i]);previous['slot_id']=other['slot_id'];other.update(previous)
            slots[i]=row
        else:raise ValueError('LITERATURE_SOURCE_SELECTION_INVALID')
        state.update(selected=_selected(slots),revision=state['revision']+1)
        state['sha256']=canonical_sha256(state)
        owner.writer._atomic_write(owner.root/'literature_sources.json',state)
    return owner.get_state()
