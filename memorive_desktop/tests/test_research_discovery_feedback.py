"""sample regression contract. Isolated records; no real accounts or model calls."""
import json
from pathlib import Path
import pytest
from memorive.desktop.api import ProductApi

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.setenv('MEMORIVE_TEST_CONSOLE_AUTO_EXECUTION','DISABLED')
    monkeypatch.setenv('MEMORIVE_TEST_CONSOLE_SESSION_ID','review-sample-regression')
    monkeypatch.delenv('WOS_API_KEY',raising=False)
    instance=ProductApi(tmp_path/'localappdata/state',ROOT/'source',ROOT/'source/facade_method_binding.json',
        session_source_roots={'codex_home':tmp_path/'empty-codex','claude_home':tmp_path/'empty-claude'})
    yield instance
    instance.close()

def test_direction_registry_is_not_channel_names():
    from memorive.research.common import defaults, validate_config
    config=defaults()
    assert 'directions' in config
    config['directions']=[{'id':'direction-a','revision':1,'name':'A','query':'alpha',
        'channels':['CORE'],'keywords':['alpha'],'seed_ids':[]},
        {'id':'direction-b','revision':1,'name':'B','query':'beta','channels':['CORE'],
         'keywords':['beta'],'seed_ids':[]}]
    assert len(validate_config(config)['directions'])==2

def test_count_is_user_reading_target_not_fixed_twenty():
    from memorive.research.common import defaults,validate_config
    c=defaults();c['count']=30
    assert validate_config(c)['count']==30

def test_library_identity_never_substring_or_report_text():
    from memorive.research.discovery.relations import exact_library_matches
    records=[{'artifact_id':'a','identifiers':{'doi':'10.1234/paper.extra'}},
             {'artifact_id':'b','summary':'mentions 10.1234/paper'}]
    assert exact_library_matches({'doi':'10.1234/paper'},records)==[]
    records.append({'artifact_id':'c','identifiers':{'doi':'https://doi.org/10.1234/PAPER'}})
    assert exact_library_matches({'doi':'10.1234/paper'},records)==['c']

def test_feedback_uses_append_only_engine(tmp_path):
    from memorive.research.recommendations.feedback_store import FeedbackStore
    store=FeedbackStore(tmp_path)
    item={'candidate_id':'candidate-a','work_cluster_id':'work-a','primary_direction_id':'direction-a',
          'direction_revision':'1','manifestation_id':'version-a','position':1,'channel':'CORE','evidence_refs':['fixture']}
    exposure=store.expose('run-a','A'*64,item,'shown-a')
    assert store.expose('run-a','A'*64,item,'shown-a')==exposure
    decision=store.decide(exposure['event_id'],'NOT_FOR_THIS_DIRECTION','decision-a')
    assert store.eligibility(item)['eligibility']=='INELIGIBLE'
    assert store.eligibility(dict(item,primary_direction_id='direction-b'))['eligibility']=='ELIGIBLE'
    store.revoke(decision['event_id'],'undo-a')
    assert FeedbackStore(tmp_path).eligibility(item)['eligibility']=='ELIGIBLE'
    assert len(store.snapshot()['events'])==3

def test_view_only_does_not_generate_exposures(tmp_path):
    from memorive.research.recommendations.feedback_store import FeedbackStore
    store=FeedbackStore(tmp_path)
    assert store.snapshot()['events']==[]


def test_research_exposes_feedback_methods(api):
    state=api.call('research.feedback_state',{})
    assert state['events']==[]

def test_config_migration_preserves_legacy_matching_and_choices(api):
    state=api.call('research.config_get',{})
    c=state['config']; c['matching_preference']=73;c['push_enabled']=False
    result=api.call('research.config_save',{'config':c,'expected_revision':state['revision']})
    assert result['config']['matching_preference']==73
    assert result['config']['push_enabled'] is False

def test_single_source_refresh_is_real_scoped_action(api):
    from memorive.research.runtime import METHODS
    assert 'research.source_refresh' in METHODS

@pytest.fixture
def discovery(api):
    from loopback_metadata import LoopbackMetadata
    fixture=LoopbackMetadata()
    for source in ('arxiv','crossref'):
        s=api.call('settings.external_sources_get',{})['literature']
        api.call('settings.external_sources_save',{'source':{'source_id':source,'selected':True},'expected_revision':s['revision']})
    api._research.client.transport=fixture
    yield api,fixture
    fixture.close()

def set_config(api,**changes):
    state=api.call('research.config_get',{})
    return api.call('research.config_save',{'config':dict(state['config'],**changes),'expected_revision':state['revision']})

def direction(identity,name,channel='CORE'):
    return {'id':'direction-'+identity,'revision':1,'name':name,'query':'Synthetic workflow',
        'channels':[channel],'keywords':[],'seed_ids':[]}


def test_task_library_message_log_single_identity(discovery):
    api,fixture=discovery
    record=api._research.run_sync('discovery','crosspage-1',topic='Synthetic workflow',count=3)
    run=record['run_id']
    assert (api._research.run_path(run)/'result.md').read_text(encoding='utf8')==record['result']['markdown']
    listed=api.call('current_task.list',{})['research_bindings']
    assert any(r['job_id']==run and len(r['workflow_nodes'])==6 for r in listed)
    assert api.call('current_task.select_task',{'run':run})['projection']['workflow_kind']=='RESEARCH_DISCOVERY'
    assert api.call('current_task.select_node',{'node_id':'ingest'})['status']=='PASS'
    message=next(r for r in api.call('messages.list',{})['rows'] if r['message_id']==record['message_id'])
    route=api.call('messages.navigate',{'message_id':message['message_id'],'locator':message['target_locator']})
    assert route['artifact_id']==run+'/result'
    logs=api.call('work_log.list_entries',{'time_range':'all'})['rows']
    assert any(r['job_id']==run for r in logs)
    statepath=api._research.run_path(run)/'state.json'
    from memorive.research.common import read,write,sealed
    write(statepath,sealed(dict(read(statepath),finished_at='2026-01-01T00:00:00Z')))
    assert next(r for r in api.call('research.task_list',{})['rows'] if r['run_id']==run)['history']


def test_publish_repair_never_repeats_acquisition(discovery,monkeypatch):
    api,fixture=discovery
    original=api._research.publish_to_library
    def failed(*args,**kwargs):raise OSError('test interrupted publication')
    monkeypatch.setattr(api._research,'publish_to_library',failed)
    item=api._research.start(kind='discovery',request_id='delivery-repair',topic='Synthetic workflow',count=2)
    api._research.thread.join(20)
    path=api._research.run_path(item['run_id'])
    before=(path/'result.json').read_bytes();calls=len(fixture.calls)
    monkeypatch.setattr(api._research,'publish_to_library',original)
    repaired=api.call('research.repair_delivery',{'run_id':item['run_id']})
    assert repaired['status']=='SUCCEEDED' and len(fixture.calls)==calls
    assert (path/'result.json').read_bytes()==before
    again=api.call('research.repair_delivery',{'run_id':item['run_id']})
    assert again['message_id']==repaired['message_id']

def test_source_refresh_is_disabled_without_receive_consent(discovery):
    api,fixture=discovery
    with pytest.raises(ValueError,match='RESEARCH_RECEIVING_DISABLED'):
        api.call('research.source_refresh',{'source':'arxiv','request_id':'only-source'})
    assert fixture.calls==[]
    set_config(api,passive_enabled=True,topic='Synthetic workflow')
    row=api.call('research.source_refresh',{'source':'arxiv','request_id':'only-source'})
    api._research.thread.join(20)
    assert fixture.calls==['ARXIV']
    assert api.call('research.get',{'run_id':row['run_id']})['status']=='SUCCEEDED'


def test_global_exclusion_requires_confirmation_and_survives_restart(tmp_path):
    from memorive.research.recommendations.feedback_store import FeedbackStore
    store=FeedbackStore(tmp_path)
    item={'candidate_id':'x','work_cluster_id':'w','primary_direction_id':'direction-a',
        'direction_revision':'1','manifestation_id':'m','position':1,'channel':'CORE','evidence_refs':['proof']}
    exp=store.expose('r','A'*64,item,'e')
    with pytest.raises(ValueError,match='CONFIRMATION'):
        store.decide(exp['event_id'],'GLOBALLY_USELESS','bad')
    store.decide(exp['event_id'],'GLOBALLY_USELESS','good',global_confirmation=True)
    assert FeedbackStore(tmp_path).eligibility(dict(item,primary_direction_id='direction-b'))['eligibility']=='INELIGIBLE'

def test_no_unauthorized_history_mining(api):
    state=api.call('research.interaction_state',{})
    assert state['status']=='DISABLED_BY_USER' and state['events']==[]
    set_config(api,use_recent_interests=True)
    state=api.call('research.interaction_state',{})
    assert state['ranking_consumed'] is False and state['historical_conversation_mining'] is False


def test_user_share_limit_never_rounded_up(discovery):
    api,_=discovery
    config=api.call('research.config_get',{})['config']
    set_config(api,topic='Synthetic workflow',count=2,exposure_limits={**config['exposure_limits'],'direction_share':.1})
    result=api._research.run_sync('discovery','share-not-rounded')
    assert result['status']=='SUCCEEDED' and result['result']['recommendations']==[]

def test_title_signal_is_not_fake_vector():
    from memorive.research.discovery.relations import metadata_comparison
    result=metadata_comparison({'candidate_id':'a','title':'Same title','identifiers':{}},
        [{'artifact_id':'b','title':'Same title','identifiers':[]}])
    assert result['library_new']=='UNKNOWN'
    assert result['relations'][0]['evidence_level']=='METADATA_ONLY'

def test_corrupt_readable_repair_does_not_overwrite_or_refetch(discovery):
    api,fixture=discovery
    result=api._research.run_sync('discovery','readable-corrupt',topic='Synthetic workflow',count=2)
    path=api._research.run_path(result['run_id'])/'result.md'
    path.write_text('External user edit',encoding='utf8');before=len(fixture.calls)
    with pytest.raises(ValueError,match='READABLE_RESULT_MISMATCH'):
        api.call('research.repair_delivery',{'run_id':result['run_id']})
    assert path.read_text(encoding='utf8')=='External user edit' and len(fixture.calls)==before

def test_failed_task_deeplink_and_repair_affordance(discovery,monkeypatch):
    api,_=discovery
    monkeypatch.setattr(api._research,'publish_to_library',lambda *a: (_ for _ in ()).throw(OSError('Injected publication failure')))
    row=api._research.start(kind='discovery',request_id='fail-deeplink',topic='Synthetic workflow',count=2)
    api._research.thread.join(20)
    from memorive.research.task_projection import bindings
    task=next(t for t in bindings(api._research) if t['run']==row['run_id'])
    assert task['delivery_repair_available'] and task['workflow_nodes'][4]['state']=='FAILED'
    messages=api.call('messages.list',{})['rows']
    message=next(m for m in messages if m['target_locator']=='memorive://job/'+row['run_id'])
    route=api.call('messages.navigate',{'message_id':message['message_id'],'locator':message['target_locator']})
    assert route['research_task_id']==row['run_id']


# When sources fail or bring nothing new

def test_one_failing_source_leaves_a_partial_result_that_names_it(discovery):
    api,fixture=discovery
    fixture.failures={'CROSSREF':503}
    record=api._research.run_sync('discovery','one-source-down',topic='Synthetic workflow',count=3)
    result=record['result']
    assert record['status']=='PARTIAL' and result['status']=='PARTIAL'
    assert {(f['source'],f['error_code']) for f in result['source_failures']}=={('crossref','SOURCE_HTTP_503')}
    assert result['recommendations'] and record['message_id']           # what arXiv returned is still delivered
    assert 'crossref: SOURCE\\_HTTP\\_503' in result['markdown'] or 'crossref: SOURCE_HTTP_503' in result['markdown']


def test_when_every_source_fails_the_run_stops_with_each_reason_and_a_message(discovery):
    api,fixture=discovery
    fixture.failures={'ARXIV':'EXTERNAL_METADATA_TIMEOUT','CROSSREF':503}
    item=api._research.start(kind='discovery',request_id='all-sources-down',topic='Synthetic workflow',count=3)
    api._research.thread.join(20)
    state=api.call('research.get',{'run_id':item['run_id']})
    assert (state['status'],state['failed_stage'],state['error_code'])==('FAILED','COLLECTING','ALL_LITERATURE_SOURCES_FAILED')
    path=api._research.run_path(item['run_id'])
    from memorive.research.common import read
    reasons={(f['source'],f['error_code']) for f in read(path/'source_failures.json')['failures']}
    assert {('arxiv','EXTERNAL_METADATA_TIMEOUT'),('crossref','SOURCE_HTTP_503')}<=reasons
    assert not (path/'result.json').exists() and 'result' not in state
    message=next(m for m in api.call('messages.list',{})['rows'] if m['target_locator']=='memorive://job/'+item['run_id'])
    assert api.call('messages.navigate',{'message_id':message['message_id'],'locator':message['target_locator']})['research_task_id']==item['run_id']


def test_a_run_that_finds_nothing_new_saves_its_result_and_sends_no_message(discovery):
    api,fixture=discovery
    first=api._research.run_sync('discovery','nothing-new-1',topic='Synthetic workflow',count=3)
    assert first['message_id']
    before=len(api.call('messages.list',{})['rows'])
    again=api._research.run_sync('discovery','nothing-new-2',topic='Synthetic workflow',count=3)
    assert again['status']=='SUCCEEDED' and again['result']['new_work_count']==0
    assert again['message_id'] is None and again['notification_status']=='SUPPRESSED_BY_USER'
    assert len(api.call('messages.list',{})['rows'])==before
    assert again['library_locator']=='memorive://artifact/'+again['run_id']+'/result'    # the result is still kept
    fixture.entries=0
    empty=api._research.run_sync('discovery','nothing-at-all',topic='Synthetic workflow',count=3)
    assert empty['status']=='SUCCEEDED' and empty['result']['recommendations']==[] and empty['message_id'] is None


def test_a_version_counts_as_new_only_when_it_was_not_known_before(tmp_path):
    """An arXiv record that names its journal DOI stands for that journal version too: the Crossref
    record of the same DOI later is not a new version. A new arXiv version, or a DOI first seen
    later, is."""
    from memorive.research.discovery.work_index import WorkIndex
    index=WorkIndex(tmp_path)
    def row(candidate,**identifiers):
        return {'candidate_id':candidate,'primary_direction_id':'direction-1','identifiers':identifiers,
                'bibliographic':{'title':'Synthetic workflow'}}
    assert index.commit_batch('run-1',[row('a',arxiv_id='2609.10000v1',doi='10.9999/sample.0')])['new_work_count']==1
    again=index.commit_batch('run-2',[row('b',doi='10.9999/sample.0')])
    assert again['recommendations']==[] and again['duplicate_bindings'][0]['reason']=='DIRECTION_ALREADY_DELIVERED'
    assert index.commit_batch('run-3',[row('c',arxiv_id='2609.10000v2',doi='10.9999/sample.0')])['new_version_count']==1
    assert index.commit_batch('run-4',[row('d',arxiv_id='2609.20000v1')])['new_work_count']==1
    # The same arXiv version again shows nothing, so its journal DOI has not been shown yet: the
    # journal record that follows is the first sight of that version.
    assert index.commit_batch('run-5',[row('e',arxiv_id='2609.20000v1',doi='10.9999/sample.2')])['recommendations']==[]
    assert index.commit_batch('run-6',[row('f',doi='10.9999/sample.2')])['new_version_count']==1


# Starting a run: what is refused, and what a repeated request gets

@pytest.mark.parametrize('params,code',[
    (dict(kind='other',request_id='r1'),'RESEARCH_START_INVALID'),
    (dict(kind='discovery',request_id=''),'RESEARCH_START_INVALID'),
    (dict(kind='discovery',request_id='r1',topic='  '),'RESEARCH_TOPIC_REQUIRED'),
    (dict(kind='discovery',request_id='r1',topic='Synthetic workflow',source_filter='wos'),'RESEARCH_SOURCE_NOT_ENABLED'),
    (dict(kind='discovery',request_id='r1',topic='Synthetic workflow',direction_id='direction-missing'),'RESEARCH_DIRECTION_NOT_FOUND'),
    (dict(kind='discovery',request_id='r1',topic='Synthetic workflow',regenerate_from='research-'+'0'*24),'REPORT_REGENERATION_INVALID'),
    (dict(kind='discovery',request_id='r1',topic='Synthetic workflow',trigger='PASSIVE_OPT_IN'),'RESEARCH_PASSIVE_AUTHORIZATION_INVALID'),
    (dict(kind='discovery',request_id='r1',topic='Synthetic workflow',trigger='PASSIVE_MATERIAL_PROGRESS'),'REPORT_MATERIAL_PROGRESS_REQUIRED'),
    (dict(kind='report',request_id='r1',period='yearly'),'RESEARCH_PERIOD_INVALID'),
    (dict(kind='report',request_id='r1',period='daily'),'REPORT_NO_LIBRARY_CHANGE'),
])
def test_a_request_that_cannot_run_is_refused_before_anything_is_written(discovery,params,code):
    api,fixture=discovery
    with pytest.raises(ValueError,match=code):
        api._research.start(**params)
    assert not list((api._research.root/'runs').glob('*/request.json')) and not fixture.calls


def test_a_discovery_without_a_selected_source_is_refused(discovery):
    api,_=discovery
    for source in ('arxiv','crossref'):
        s=api.call('settings.external_sources_get',{})['literature']
        api.call('settings.external_sources_save',{'source':{'source_id':source,'selected':False},'expected_revision':s['revision']})
    with pytest.raises(ValueError,match='RESEARCH_SOURCE_REQUIRED'):
        api._research.start(kind='discovery',request_id='no-source',topic='Synthetic workflow')


def test_a_report_regenerates_only_an_earlier_report_of_its_period(discovery):
    api,_=discovery
    run=api._research.run_sync('discovery','not-a-report',topic='Synthetic workflow',count=2)
    with pytest.raises(ValueError,match='REPORT_REGENERATION_INVALID'):
        api._research.start(kind='report',request_id='regenerate',period='daily',regenerate_from=run['run_id'])


def test_a_repeated_request_gets_its_first_run_and_a_changed_one_is_a_conflict(discovery):
    api,_=discovery
    first=api._research.run_sync('discovery','same-request',topic='Synthetic workflow',count=2)
    assert api._research.start(kind='discovery',request_id='same-request',topic='Synthetic workflow',count=2)['run_id']==first['run_id']
    with pytest.raises(ValueError,match='RESEARCH_IDEMPOTENCY_CONFLICT'):
        api._research.start(kind='discovery',request_id='same-request',topic='Another topic',count=2)


def test_while_a_run_is_active_only_the_same_discovery_joins_it(discovery,monkeypatch):
    api,_=discovery
    running=api._research.run_sync('discovery','active-run',topic='Synthetic workflow',count=2)
    monkeypatch.setattr(api._research,'has_active_run',lambda:True)
    api._research.current=running['run_id']
    with pytest.raises(ValueError,match='RESEARCH_ALREADY_RUNNING'):
        api._research.start(kind='discovery',request_id='another-run',topic='Another topic',count=2)
    assert api._research.start(kind='discovery',request_id='joining-run',topic='Synthetic workflow',count=2)['run_id']==running['run_id']


def test_a_closed_runtime_starts_nothing(discovery,monkeypatch):
    api,_=discovery
    monkeypatch.setattr(api._research,'closed',True)
    with pytest.raises(ValueError,match='RESEARCH_CLOSED'):
        api._research.start(kind='discovery',request_id='after-close',topic='Synthetic workflow')


def test_only_a_listed_link_of_a_shown_paper_opens(discovery,monkeypatch):
    api,_=discovery
    run=api._research.run_sync('discovery','links',topic='Synthetic workflow',count=2)
    candidate=run['result']['recommendations'][0]['candidate_id']
    opened=[]
    import dataclasses
    monkeypatch.setattr(api._research,'host',dataclasses.replace(api._research.host,open_target=lambda target:opened.append(target) or {'status':'OPENED'}))
    with pytest.raises(ValueError,match='RESEARCH_SOURCE_LINK_INVALID'):
        api.call('research.open_source',{'run_id':run['run_id'],'candidate_id':candidate,'url':'https://example.org/elsewhere'})
    with pytest.raises(ValueError,match='RESEARCH_CANDIDATE_NOT_FOUND'):
        api.call('research.expose',{'run_id':run['run_id'],'candidate_id':'missing','request_id':'expose-1'})
    assert opened==[]
