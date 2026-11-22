"""Real local config/package/identity/cursor contracts; no browser account."""
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_extension_upgrade_repair_gate import _api,_write_state,_write_identity,_write_preflight
from memorive.sessions.companion.site_profiles import SiteProfiles,default_sites,normalize_sites,profile_hash,configured_extension
from memorive.sessions.companion.incremental_cursor import build_incremental_cursor

def test_config_change_invalidates_identity_and_previous_preflight(tmp_path):
    api,downloads,state_path=_api(tmp_path);_write_state(api,state_path);_write_identity(downloads);_write_preflight(downloads)
    assert api.browser_companion_status()['bridge_identity_verified']
    rows=api.browser_companion_sites()['sites'];rows[1]['enabled']=False
    api.save_browser_companion_sites(rows,0)
    status=api.browser_companion_status()
    assert not status['bridge_identity_verified'] and not status['full_sync_eligible']
    assert status['providers']==['deepseek','kimi']

def test_current_hash_identity_then_enabled_only_preflight(tmp_path):
    api,downloads,state_path=_api(tmp_path)
    rows=default_sites();rows[1]['enabled']=False
    config=SiteProfiles(api.profile_path('BROWSER_COMPANION')).save(rows,0)
    _write_state(api,state_path);_write_identity(downloads);_write_preflight(downloads)
    for path in downloads.rglob('*.json'):
        data=json.loads(path.read_text())
        data['site_profile_sha256']=config['profile_sha256']
        if data['schema_version'].endswith('IDENTITY_V1'):
            data['providers']=['deepseek','kimi']
        else:
            data['active_provider_count']=2;data['providers']=[p for p in data['providers'] if p['provider_id']!='gemini']
        path.write_text(json.dumps(data))
    status=api.browser_companion_status()
    assert status['bridge_identity_verified'] and status['full_sync_eligible']
    assert set(status['full_sync_gate']['providers'])=={'deepseek','kimi'}
    assert 'gemini' not in api._browser_sync_trigger('test')

def test_missing_or_wrong_profile_hash_cannot_prove_identity(tmp_path):
    api,downloads,state_path=_api(tmp_path);_write_state(api,state_path);_write_identity(downloads)
    identity=next(downloads.rglob('*identity*.json'))
    for hash_value in (None,'F'*64):
        data=json.loads(identity.read_text())
        if hash_value is None: data.pop('site_profile_sha256',None)
        else:data['site_profile_sha256']=hash_value
        identity.write_text(json.dumps(data))
        assert not api.browser_companion_status()['bridge_identity_verified']


def test_all_off_no_sites_no_capture_no_full_gate(tmp_path):
    api,downloads,state_path=_api(tmp_path)
    rows=default_sites()
    for row in rows:row['enabled']=False
    api.save_browser_companion_sites(rows,0)
    assert api.run_browser_companion_test_read()['status']=='BROWSER_NO_ENABLED_SITES'
    assert api.run_browser_companion_sync('INCREMENTAL')['status']=='BROWSER_NO_ENABLED_SITES'
    status=api.browser_companion_status()
    assert not status['preflight_eligible'] and not status['full_sync_eligible']
    source,_,_=api._browser_companion_package()
    # With every sync site off the extension still records the official AI websites, nothing else.
    from memorive.sessions.companion.site_profiles import capture_patterns
    assert json.loads((source/'manifest.json').read_text(encoding='utf-8'))['host_permissions']==capture_patterns()

def test_site_save_revision_lock_and_history(tmp_path):
    s=SiteProfiles(tmp_path); rows=default_sites();rows[0]['enabled']=False
    saved=s.save(rows,0)
    with pytest.raises(ValueError,match='REVISION'):s.save(rows,0)
    assert s.get()['profile_sha256']==profile_hash(rows)
    assert len(list(tmp_path.glob('sites_before*.json')))==1

def test_explicit_incremental_never_silently_launches_test(tmp_path):
    api,_,_=_api(tmp_path)
    api.browser_companion_status=lambda:dict(prepared=True,bridge_identity_verified=True,preflight_eligible=True,capture_repair_preflight_required=True)
    api._launch_browser_companion_sync_fragment=lambda *a,**k:pytest.fail('UNEXPECTED_READ')
    assert api.run_browser_companion_sync('INCREMENTAL')['status']=='PREFLIGHT_REQUIRED'
