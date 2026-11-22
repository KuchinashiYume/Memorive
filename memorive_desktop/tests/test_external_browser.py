"""SHARED sample feedback regressions; fixtures only, no private capture."""
from copy import deepcopy
import json
import pytest
from memorive.desktop.settings.external_sources import ExternalDataSources
from memorive.models.validation.transport import HttpResponse
from legacy_aa_fixture import configure_aa

class AA:
    def __init__(self): self.calls=[]; self.fail_page=0
    def request(self,**kwargs):
        self.calls.append(kwargs)
        n=len(self.calls)
        if self.fail_page==n: return HttpResponse(status_code=503,body=b'{}')
        return HttpResponse(status_code=200,body=json.dumps(dict(tier='free',intelligence_index_version=4.1,
            pagination=dict(page=n,page_size=1,total_pages=2,has_more=n<2),
            data=[dict(id=f'm{n}',slug=f'model-{n}',name=f'Model {n}',
                       evaluations=dict(artificial_analysis_intelligence_index=50+n))])).encode())
def test_aa_follows_documented_pagination_and_keeps_page_receipts(tmp_path):
    transport=AA(); svc=ExternalDataSources(tmp_path,transport=transport,environment={'ARTIFICIAL_ANALYSIS_API_KEY':'fixture'})
    configure_aa(svc)
    r=svc.refresh('artificial-analysis',expected_revision=svc.get_state()['revision'])
    assert r['status']=='PASS' and r['record_count']==2
    assert r['external_network_calls']==2 and '?page=2' in transport.calls[1]['url']
    assert len(list((tmp_path/'receipts').glob('*.page*.presend.json')))==2
    assert all(x['headers']['x-api-key']=='fixture' for x in transport.calls)
    assert 'fixture' not in ''.join(p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))
def test_site_profiles_exact_hosts_and_disabled_scope():
    from memorive.sessions.companion.site_profiles import default_sites, normalize_sites, profile_hash, active_sites
    rows=default_sites(); rows[1]['enabled']=False
    assert [r['provider_id'] for r in active_sites(normalize_sites(rows))]==['deepseek','kimi']
    assert profile_hash(rows)!=profile_hash(default_sites())
    for row in rows: row['enabled']=False
    assert active_sites(normalize_sites(rows))==[]
def test_custom_site_identity_cannot_masquerade_as_builtin():
    from memorive.sessions.companion.site_profiles import default_sites, normalize_sites
    rows=default_sites(); rows[0].update(adapter='generic',name='Another AI',url='https://ai.example.org/chat/')
    normalized=normalize_sites(rows)
    assert normalized[0]['provider_id'].startswith('web-')
    assert normalized[0]['hosts']==['ai.example.org']
    rows[0]['url']='https://user:secret@example.org/'
    with pytest.raises(ValueError): normalize_sites(rows)
@pytest.mark.parametrize('url',['http://ai.example.org/','https://localhost/','https://127.0.0.1/','https://192.168.0.3/','https://example.org/?key=private','https://*.example.org/','https://example.org:444/'])
def test_custom_site_rejects_unsafe_addresses(url):
    from memorive.sessions.companion.site_profiles import default_sites, normalize_sites
    rows=default_sites(); rows[0].update(adapter='generic',url=url)
    with pytest.raises(ValueError): normalize_sites(rows)
