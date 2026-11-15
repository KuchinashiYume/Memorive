"""sample isolated failures and successors; no account/model requests."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cli_settings_usability import _controller, _configured_cli_services
from test_research_discovery_feedback import api
from memorive_library.paper_review import PaperReview
from memorive_library.product_controller import LibraryProductController
from memorive_research_runtime.periodic import windows
from memorive_research_runtime.common import read

ROOT=Path(__file__).resolve().parents[1]


def configured(tmp_path):
    controller=_controller(tmp_path)
    controller.save_cli_services(cli_services=_configured_cli_services(controller),mode='ADVANCED',
                                expected_revision=controller.get_state()['revision'])
    service=controller.get_state()['settings']['cli_services'][0]
    return controller, {'config_id':service['config_id'],'profile_ref':service['models'][0]['profile_ref']}


def finished(controller, job):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        row=controller.cli_verification_status(job_id=job['job_id'])
        if row['state']!='RUNNING':return row
        time.sleep(.02)
    raise AssertionError('test worker did not finish')


def test_cli_long_request_not_limited_by_ipc_and_preserves_unrelated_settings(tmp_path):
    controller,params=configured(tmp_path); calls=[];entered=threading.Event();release=threading.Event()
    def execute(service,model):
        calls.append(model['model_name']);entered.set();assert release.wait(10)
        return {'status':'AVAILABLE','reason':'CLI_MINIMAL_REQUEST_COMPLETED',
                'requested_model':model['model_name'],'returned_model':model['model_name']}
    controller.model_validation_runner=SimpleNamespace(verify_cli_model=execute)
    started=time.monotonic()
    job=controller.start_cli_verification(**params,idempotency_key='same-request-104-abcdef')
    assert time.monotonic()-started<1
    assert entered.wait(1)
    try:
        time.sleep(5.5)  # intentionally longer than the observed inherited IPC deadline
        assert controller.cli_verification_status(job_id=job['job_id'])['state']=='RUNNING'
        assert controller.start_cli_verification(**params,idempotency_key='same-request-104-abcdef')['job_id']==job['job_id']
        current=controller.store.load();changed=deepcopy(current['settings']);changed['preferences']['language']='ja-JP'
        controller.store.save(changed,expected_revision=current['revision'])
    finally:release.set()
    result=finished(controller,job)
    assert result['state']=='COMPLETE',result
    assert result['receipt']['status']=='AVAILABLE'
    assert len(calls)==1
    assert controller.get_state()['settings']['preferences']['language']=='ja-JP'


def test_cli_target_change_never_applies_old_success(tmp_path):
    controller,params=configured(tmp_path);entered=threading.Event();release=threading.Event()
    def execute(*args):
        entered.set();release.wait(5)
        return {'status':'AVAILABLE','reason':'CLI_MINIMAL_REQUEST_COMPLETED'}
    controller.model_validation_runner=SimpleNamespace(verify_cli_model=execute)
    job=controller.start_cli_verification(**params,idempotency_key='changed-target-104-abcdef')
    assert entered.wait(1)
    current=controller.store.load();settings=deepcopy(current['settings'])
    settings['cli_services'][0]['models'][0]['model_name']='different-model'
    controller.store.save(settings,expected_revision=current['revision']);release.set()
    assert finished(controller,job)['state']=='ERROR'
    assert controller.get_state()['settings']['cli_services'][0]['models'][0]['connection_status']=='UNVERIFIED'


def test_cli_worker_loss_does_not_repeat_model_request(tmp_path):
    controller,params=configured(tmp_path)
    jobs=controller.cli_verification_jobs
    row={'job_id':'a'*32,'profile_ref':params['profile_ref'],'state':'RUNNING','started_at':'2026-09-08T00:00:00Z'}
    jobs._write(row)
    assert jobs.status(job_id=row['job_id'])['state']=='INTERRUPTED'
    assert controller.metrics['model_validation_requests']==0


@pytest.fixture
def paper(tmp_path):
    source=tmp_path/'Source.pdf';source.write_bytes(b'%PDF synthetic')
    document='core-product-'+'a'*24;entries={};original={}
    for i,kind in enumerate(('CORE_CARD','CORE_ANALYSIS')):
        path=tmp_path/('Card.md' if i==0 else 'Analysis.md');path.write_text('# Original English content '+str(i),encoding='utf8')
        key='core-product-'+str(i)*24
        original[path]=path.read_bytes()
        entries[key]={'job_id':'job-review','artifact_kind':kind,'artifact_root':str(tmp_path),
            'private_path':str(path),'content_sha256':hashlib.sha256(path.read_bytes()).hexdigest().upper(),
            'source_root':str(tmp_path),'source_private_path':str(source),
            'source_content_sha256':hashlib.sha256(source.read_bytes()).hexdigest().upper()}
    children=list(entries);entries[document]=dict(entries[children[0]],artifact_kind='CORE_DOCUMENT')
    lib=SimpleNamespace(state_root=tmp_path,_core_entries_by_artifact_id=entries,
        _core_row_aliases={key:document for key in children},_utc_now=lambda:'2026-09-08T00:00:00Z',
        _read_core_entry_file=LibraryProductController._read_core_entry_file)
    return PaperReview(lib),document,children,original


def test_paper_review_requires_current_card_and_analysis_then_persists(paper):
    review,document,children,original=paper
    with pytest.raises(ValueError):review.confirm(document)
    review.open(children[0]);assert not review.metadata(document)['review_opened']
    review.open(children[1]);assert review.metadata(document)['review_opened']
    receipt=review.confirm(document);assert receipt['status']=='PASS'
    assert all(state=='复核通过' for state in receipt['review']['file_review_states'].values())
    restarted=PaperReview(review.library);assert restarted.metadata(document)['review_confirmed']
    assert review.confirm(document)==receipt
    assert all(path.read_bytes()==data for path,data in original.items())


def test_paper_changed_file_revokes_confirmation_without_overwriting_evidence(paper):
    review,document,children,original=paper
    for child in children:review.open(child)
    review.confirm(document);frozen=review.path.read_bytes()
    next(iter(original)).write_text('changed by user',encoding='utf8')
    assert not review.metadata(document)['review_confirmed']
    assert not review.metadata(document)['review_files_valid']
    with pytest.raises(ValueError):review.confirm(document)
    assert review.path.read_bytes()==frozen


def test_windows_only_completed_material_intervals_no_fixed_lookback():
    clock=datetime.fromisoformat('2026-09-08T00:00:00+08:00')
    assert windows([],clock)==[]
    rows=windows([{'updated_at':'2026-08-15T00:00:00Z'},{'updated_at':'2026-09-08T02:00:00+08:00'}],clock)
    assert {r['window']['period'] for r in rows}=={'daily','weekly','monthly'}
    assert len(rows)==3
    assert all(len(r['progress'])==1 for r in rows)


def test_fractional_days_preserve_existing_hours():
    from memorive_research_runtime.common import defaults,validate_config
    config=defaults()
    for hours in (1,7,24,49,72,0.125*24):
        config['interval_hours']=(hours/24)*24
        assert validate_config(config)['interval_hours']==hours


