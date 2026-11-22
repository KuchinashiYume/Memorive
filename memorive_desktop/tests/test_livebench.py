"""LiveBench reference adapter: synthetic data, no inference or private input."""
from copy import deepcopy
import csv
import io
import json
import pytest
from memorive.desktop.settings.external_sources import ExternalDataSources,default_sources,normalize_payload,role_references
from memorive.desktop.settings.livebench_reference import CATEGORY_METRICS,release_urls,DEFAULT_ENDPOINT
from memorive.models.validation.transport import HttpResponse
from legacy_aa_fixture import configure_aa
from memorive.platform.json_files import canonical_sha256

def bundle(models=None):
    categories={name:[f'task_{i}'] for i,name in enumerate(CATEGORY_METRICS)}
    out=io.StringIO(); writer=csv.writer(out)
    writer.writerow(['model',*[t[0] for t in categories.values()]])
    for name,value in (models or [('gpt-5.6-sol-max',80),('deepseek-v4-flash',60),('qwen3.8-27b',70)]):
        writer.writerow([name,*([value]*7)])
    return dict(release='2026-06-25',categories=categories,table_csv=out.getvalue())

class Transport:
    def __init__(self): self.calls=[]; self.fail=0
    def request(self,**kw):
        self.calls.append(kw); n=(len(self.calls)-1)%3
        if len(self.calls)==self.fail: return HttpResponse(503,b'not reflected')
        data=bundle()
        body=[b"useState('2026-06-25'); setSelectedDate('2026-01-01'); setSelectedDate('2026-06-25');",
            json.dumps(data['categories']).encode(),data['table_csv'].encode()][n]
        return HttpResponse(200,body)

def test_default_is_livebench_with_no_credentials():
    row=default_sources()[0]
    assert row['display_name']=='LiveBench' and row['format']=='LIVEBENCH_RELEASE'
    assert not row['credential_env'] and row['endpoint']==DEFAULT_ENDPOINT


def test_category_means_not_weighted_by_number_of_tasks():
    data=bundle([('a',80)])
    data['categories']['Reasoning']=['task_0','extra']
    data['table_csv']='model,task_0,task_1,task_2,task_3,task_4,task_5,task_6,extra\na,0,70,70,70,70,70,70,0\n'
    row=normalize_payload(default_sources()[0],data)[0]
    assert row['metrics']['livebench_global']==60

def test_missing_tasks_never_fabricate_complete_global():
    data=bundle([('a',80)])
    data['table_csv']=data['table_csv'].replace('80,80',',80',1)
    row=normalize_payload(default_sources()[0],data)[0]
    assert row['metrics']['livebench_global'] is None and not row['complete_category_coverage']

def test_exact_effort_preferred_and_ambiguous_variants_not_arbitrarily_selected():
    rows=normalize_payload(default_sources()[0],bundle([('gpt-5.6-sol-max',80),('gpt-5.6-sol-high',70)]))
    assert role_references('ANALYSIS_PRIMARY',{'model_name':'gpt-5.6-sol'},rows)['references']==[]
    matched=role_references('ANALYSIS_PRIMARY',{'model_name':'gpt-5.6-sol','thinking_mode':'high'},rows)['references']
    assert len(matched)==1 and matched[0]['metrics']['livebench_global']==70

def test_local_quantization_is_only_a_base_reference():
    rows=normalize_payload(default_sources()[0],bundle())
    row=role_references('CARD_DISTILLER',{'kind':'LOCAL','model_name':'[本地] qwen3.8:27b-q4_K_M'},rows)['references'][0]
    assert row['model_id']=='qwen3.8-27b'
    assert row['quantized_profile_assessed'] is False

def test_legacy_default_migration_retains_backup_other_modes_and_no_key_binding(tmp_path):
    s=configure_aa(ExternalDataSources(tmp_path,environment={}))
    current=s.get_state(); other=current['sources'][2]['config'];other['refresh_mode']='MANUAL'
    s.save(other,expected_revision=0)
    assert s.enable_weekly_updates()['status']=='MIGRATED'
    configs=[x['config'] for x in s.get_state()['sources']]
    assert configs[0]['format']=='LIVEBENCH_RELEASE' and not configs[0]['credential_env']
    assert configs[2]['refresh_mode']=='MANUAL'
    assert len(list((tmp_path/'policy_migrations').glob('*.json')))==1
    assert s.enable_weekly_updates()['status']=='UNCHANGED'

def test_user_custom_source_is_not_replaced(tmp_path):
    s=configure_aa(ExternalDataSources(tmp_path,environment={}))
    row=s.get_state()['sources'][0]['config'];row['display_name']='My data'
    s.save(row,expected_revision=0)
    assert s.enable_weekly_updates()['status']=='UNCHANGED'
    assert s.get_state()['sources'][0]['config']['display_name']=='My data'

@pytest.mark.parametrize('bad',['nan','inf','-1','101'])
def test_invalid_score_rejected(bad):
    data=bundle();data['table_csv']=data['table_csv'].replace(',80,',','+bad+',',1)
    with pytest.raises(ValueError): normalize_payload(default_sources()[0],data)

def test_remote_javascript_is_not_executed():
    release,urls=release_urls(DEFAULT_ENDPOINT,b"alert('no'); setSelectedDate('2026-06-25');")
    assert release=='2026-06-25' and len(urls)==3
    assert all(u.startswith('https://raw.githubusercontent.com/LiveBench/livebench.github.io/main/') for u in urls)


def test_a_source_saved_with_the_removed_livebench_address_reads_from_the_new_one(tmp_path):
    # LiveBench removed its data repository (new-livebench) in November 2026; the tables are now in the
    # repository of its site, with the release dates in src/App.js.
    s=ExternalDataSources(tmp_path,environment={})
    state=s._load()
    state['sources'][0]['endpoint']='https://raw.githubusercontent.com/LiveBench/new-livebench/main/src/lib/constants.js'
    state['sha256']=canonical_sha256(state)
    s.writer.write_json(s.path,state)
    assert s.get_state()['sources'][0]['config']['endpoint']==DEFAULT_ENDPOINT
    release,urls=release_urls(DEFAULT_ENDPOINT,b"const [d,setSelectedDate]=useState('2026-06-25');\nsetSelectedDate('2024-06-24');")
    assert release=='2026-06-25' and urls[2].endswith('/LiveBench/livebench.github.io/main/public/table_2026_06_25.csv')

