"""UI gets a read-only catalog; backend remains the security authority."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_extension_upgrade_repair_gate import _api
from memorive.sessions.companion.site_profiles import OFFICIAL_WEB_SITES, BUILTINS


def test_catalog_is_not_mutable_authority(tmp_path):
    api,_,_=_api(tmp_path)
    state=api.browser_companion_sites()
    state['official_site_catalog'][0]['hosts'].append('evil.example.org')
    assert 'evil.example.org' not in api.browser_companion_sites()['official_site_catalog'][0]['hosts']
    state['sites'][0].update(url='https://evil.example.org/',adapter='generic')
    with pytest.raises(ValueError,match='NOT_VERIFIED_OFFICIAL'):
        api.save_browser_companion_sites(state['sites'],0)

def test_catalog_does_not_override_builtin_mismatch(tmp_path):
    api,_,_=_api(tmp_path)
    state=api.browser_companion_sites()
    state['sites'][0]['url']='https://chat.qwen.ai/'
    with pytest.raises(ValueError,match='ADAPTER_ORIGIN_MISMATCH'):
        api.save_browser_companion_sites(state['sites'],0)
