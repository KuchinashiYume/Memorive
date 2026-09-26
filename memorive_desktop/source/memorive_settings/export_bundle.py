"""Versioned settings bundle. Validated as a whole, recoverable after interruption."""
from copy import deepcopy
from pathlib import Path
import json, os
from .contracts import canonical_sha256, scan_sensitive
from .external_sources import validate_source, _origin
from .leaderboard import validate_preferences, PREF_SCHEMA
from . import literature_sources

def paths(owner):
    root=owner.store.profile_root
    return {'settings':owner.store.path,'external_sources':owner.external_data_sources.path,
            'literature':owner.external_data_sources.root/'literature_sources.json',
            'leaderboard':root/'leaderboard_preferences.json','research':root/'research_preferences.json'}

def recover(owner):
    path=owner.store.profile_root/'settings_import_transaction.json'
    if not path.exists():return
    tx=json.loads(path.read_text(encoding='utf8'))
    if tx.get('sha256')!=canonical_sha256({k:v for k,v in tx.items() if k!='sha256'}):raise ValueError('SETTINGS_IMPORT_RECOVERY_HASH_INVALID')
    if tx['state']=='PREPARED':
        for key,target in paths(owner).items():
            before=tx['before'][key]
            if before is None:
                target.unlink(missing_ok=True)
            else:owner.store._atomic_write(target,before)
        tx['state']='ROLLED_BACK';tx.pop('sha256',None);tx['sha256']=canonical_sha256(tx);owner.store._atomic_write(path,tx)

def extensions(owner):
    from memorive_research_runtime.common import defaults, validate_config, read
    research=paths(owner)['research']
    return {'external_sources':owner.external_data_sources._load()['sources'],
            'literature_selected':literature_sources.load(owner.external_data_sources.root)['selected'],
            'leaderboard':owner._leaderboard_service()._load()['preferences'],
            'research':validate_config(read(research)['config']) if research.exists() else defaults()}

def validate(owner,value):
    from memorive_research_runtime.common import validate_config
    if not isinstance(value,dict) or set(value)!={'external_sources','literature_selected','leaderboard','research'}:raise ValueError('SETTINGS_IMPORT_EXTENSIONS_INVALID')
    if not isinstance(value['external_sources'],list):raise ValueError('SETTINGS_IMPORT_SOURCES_INVALID')
    sources=[validate_source(s) for s in value['external_sources']]
    current=owner.external_data_sources._load()['sources'];old={s['source_id']:s for s in current}
    if len(sources)!=len(old) or {s['source_id'] for s in sources}!=set(old):raise ValueError('SETTINGS_IMPORT_SOURCE_SLOTS_INVALID')
    for row in sources:
        if row['credential_env'] and _origin(row['endpoint'])!=_origin(old[row['source_id']]['endpoint']):raise ValueError('EXTERNAL_SOURCE_CREDENTIAL_ORIGIN_REBIND_REQUIRED')
    selected=value['literature_selected']
    if not isinstance(selected,list) or any(not isinstance(x,str) or x not in literature_sources.SELECTABLE for x in selected) or len(set(selected))!=len(selected):raise ValueError('SETTINGS_IMPORT_LITERATURE_INVALID')
    return dict(external_sources=sources,literature_selected=sorted(selected),leaderboard=validate_preferences(value['leaderboard']),research=validate_config(value['research']))

def import_bundle(owner,payload,expected_revision,confirmed_risk_ids):
    from memorive_research_runtime.common import sealed, read
    accepted=validate(owner,payload['extensions'])
    source=owner.external_data_sources;leader=owner._leaderboard_service()
    with leader.lock,source._write_lock(),source._file_lock('leaderboard_preferences.lock'):
        current=owner.store.load(recover_corruption=False)
        if current['revision']!=expected_revision:raise ValueError('SETTINGS_REVISION_CONFLICT')
        targets=paths(owner)
        before={k:json.loads(p.read_text(encoding='utf8')) if p.exists() else None for k,p in targets.items()}
        tx={'schema_version':'SettingsImportTransaction-v1','state':'PREPARED','before':before}
        tx['sha256']=canonical_sha256(tx);path=owner.store.profile_root/'settings_import_transaction.json'
        owner.store._atomic_write(path,tx)
        try:
            receipt=owner.save(payload['settings'],expected_revision=expected_revision,confirmed_risk_ids=confirmed_risk_ids)
            ext=source._load();ext.update(sources=accepted['external_sources'],revision=ext['revision']+1)
            lit=literature_sources.load(source.root);lit.update(selected=accepted['literature_selected'],revision=lit['revision']+1)
            pref=leader._load();pref.update(preferences=accepted['leaderboard'],revision=pref['revision']+1)
            for key,value in (('external_sources',ext),('literature',lit),('leaderboard',pref)):
                value.pop('sha256',None)
                value['sha256']=canonical_sha256(value);owner.store._atomic_write(targets[key],value)
            prior=read(targets['research']) if targets['research'].exists() else {'revision':0}
            owner.store._atomic_write(targets['research'],sealed({'schema_version':'ResearchPreferences-v1','revision':prior['revision']+1,'config':accepted['research']}))
            tx.update(state='COMMITTED');tx.pop('sha256',None);tx['sha256']=canonical_sha256(tx);owner.store._atomic_write(path,tx)
        except Exception:
            # Recovery uses the same durable before-images, including absent files.
            recover(owner)
            raise
    receipt.update(action='IMPORT_REDACTED',included_extensions=list(accepted),transaction_status='COMMITTED')
    return receipt
