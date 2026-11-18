"""Research opportunities across processed papers: fact sheets, checks, the run and the ledger.

Three made-up papers: two report calibration error after temperature scaling on different data
(0.05 on synthetic reports, 0.11 on real clinical notes), the third states that reports with tables
were not tested. The model is a stub that answers with prepared opportunities; the checks, the run,
the library row, the message, the ledger and the weekly report are the real ones.
"""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from memorive.research.opportunities import facts, finder, runs


def card(title, *, question, objects, methods, context, data, results, conclusion, limits=(), outlook=(), status='active'):
    value = {'title': title, 'review_status': status, 'research_question': question, 'research_object': list(objects),
             'method': list(methods), 'comparison_context': [context], 'key_data': list(data),
             'key_results': results, 'author_conclusion': conclusion, 'boundary_conditions': [],
             'author_limitations_outlook': {'limitations': [{'quote': q, 'chunk_id': 'c3'} for q in limits],
                                            'outlook': list(outlook)},
             'source_anchor': {'by_field': {'research_question': [{'chunk_id': 'c1', 'quote': question}]}}}
    return '---\n' + yaml.safe_dump(value, allow_unicode=True) + '---\n\n# ' + title + '\n'


PAPERS = {
    'job-a': card('Calibrating report classifiers on synthetic data', question='Does temperature scaling calibrate report classifiers?',
                  objects=['synthetic radiology reports'], methods=['temperature scaling'],
                  context={'object': 'synthetic reports', 'scale': '1,200 reports'},
                  data=[{'metric': 'expected calibration error', 'value': '0.05', 'unit': 'unitless', 'sample': '1,200 reports',
                         'chunk_id': 'c2', 'quote': 'ECE fell to 0.05 after temperature scaling'}],
                  results='Temperature scaling lowered the calibration error.', conclusion='Temperature scaling is sufficient.'),
    'job-b': card('Calibration of clinical note classifiers', question='How well does temperature scaling calibrate clinical notes?',
                  objects=['real clinical notes'], methods=['temperature scaling'],
                  context={'object': 'real clinical notes', 'scale': '900 notes'},
                  data=[{'metric': 'expected calibration error', 'value': '0.11', 'unit': 'unitless',
                         'sample': '900 notes | phase/time: 未给 | comparator: Not given', 'chunk_id': 'c2', 'quote': 'ECE remained 0.11'},
                        {'metric': 'false recall', 'value': '39%→33%', 'unit': '%', 'chunk_id': 'c2'}],
                  results='Calibration error stayed high.', conclusion='Temperature scaling is not enough for real notes.'),
    'job-c': card('Reading long reports', question='Does evidence drift over long reports?', objects=['long reports'],
                  methods=['attention probing'], context={'object': 'long reports'}, data=[],
                  results='Evidence drifts after 4,000 tokens.', conclusion='Long reports need anchoring.',
                  limits=['We did not test reports that contain tables.']),
}


def library(tmp_path, papers=PAPERS, status=None):
    records, files = {}, {}
    for job, text in papers.items():
        if status and job in status:
            text = text.replace('review_status: active', 'review_status: ' + status[job])
        card_path = tmp_path / 'library' / job / 'card.md'
        card_path.parent.mkdir(parents=True, exist_ok=True)
        card_path.write_text(text, encoding='utf-8')
        chunks = card_path.parent / 'chunks.jsonl'
        chunks.write_text('\n'.join(json.dumps({'chunk_id': c, 'text': 'chunk ' + c, 'page_start': n, 'section_path': 'Results'})
                                    for n, c in enumerate(('c1', 'c2', 'c3'), 1)), encoding='utf-8')
        for kind, path in (('CORE_CARD', card_path), ('CORE_CHUNKS', chunks)):
            artifact = f'{job}/{kind}'
            records[artifact] = {'artifact_kind': kind, 'job_id': job, 'content_sha256': f'{job}-{kind}-sha',
                                 'private_path': str(path), 'row': {'display_name': job}}
            files[artifact] = path
    return SimpleNamespace(core_artifacts=lambda: copy.deepcopy(records), resolve_core_private_artifact=files.__getitem__,
                           records=records)


def ids(sheets):
    """fact id by (paper title, field) for the tests below: the first fact of each field."""
    found = {}
    for s in sheets:
        for f in s['facts']:
            found.setdefault((s['title'], f['field']), f['id'])
    return found


# Fact sheets

def test_a_fact_sheet_holds_what_the_card_says_with_its_place_in_the_paper(tmp_path):
    lib = library(tmp_path)
    sheets = facts.papers(lib.core_artifacts(), lib.resolve_core_private_artifact)
    assert [s['paper'] for s in sheets] == ['P1', 'P2', 'P3']
    first = sheets[0]
    data = next(f for f in first['facts'] if f['field'] == 'key_data')
    assert data['text'] == 'expected calibration error：0.05（1,200 reports）'
    assert data['quote'] == 'ECE fell to 0.05 after temperature scaling'
    assert (data['page'], data['section']) == (2, 'Results')
    assert next(f for f in first['facts'] if f['field'] == 'comparison_context')['text'] == '对象：synthetic reports；尺度：1,200 reports'
    assert first['library_files'] == [{'id': 'job-a/CORE_CARD', 'content_hash': 'job-a-CORE_CARD-sha'}]
    second = [f['text'] for f in sheets[1]['facts'] if f['field'] == 'key_data']
    assert second == ['expected calibration error：0.11（900 notes）', 'false recall：39%→33%']    # nothing "not given", no second %
    third = sheets[2]
    assert [f['text'] for f in third['facts'] if f['field'] == 'limitation'] == ['We did not test reports that contain tables.']


@pytest.mark.parametrize('state', ['pending', 'quarantined'])
def test_only_an_admitted_card_gives_a_fact_sheet(tmp_path, state):
    lib = library(tmp_path, status={'job-b': state})
    sheets = facts.papers(lib.core_artifacts(), lib.resolve_core_private_artifact)
    assert [s['title'] for s in sheets] == ['Calibrating report classifiers on synthetic data', 'Reading long reports']


# Groups and checks

def paper(number, words):
    return {'paper': f'P{number}', 'facts': [{'field': 'research_question', 'text': words}]}


def test_papers_on_related_questions_share_a_group_of_at_most_twelve():
    papers = [paper(n, 'calibration of report classifiers' if n % 2 else 'optical character recognition of forms') for n in range(1, 16)]
    groups = finder.groups(papers)
    assert [len(g) for g in groups] == [12, 3]
    first = {p['paper'] for p in groups[0]}
    odd = {f'P{n}' for n in range(1, 16, 2)}
    assert odd <= first    # all eight calibration papers are in the first group


def divergence(a, b, **changes):
    item = {'kind': 'divergence', 'title': 'Two papers disagree on how far temperature scaling lowers calibration error',
            'question': 'Does the data source explain the gap between 0.05 and 0.11?',
            'reasoning': 'Both use temperature scaling; one reports 0.05, the other 0.11.',
            'dimensions': [{'dimension': 'method', 'status': 'same', 'note': 'Both use temperature scaling.', 'fact_ids': [a['method'], b['method']]},
                           {'dimension': 'sample', 'status': 'different', 'note': 'Synthetic reports against real notes.',
                            'fact_ids': [a['comparison_context'], b['comparison_context']]}],
            'suggestion': 'Run both on the same 900 notes.', 'limitations': 'The cards give no variance.',
            'fact_ids': [a['key_data'], b['key_data']]}
    item.update(changes)
    return item


def gap(c):
    return {'kind': 'gap', 'title': 'Reports with tables are untested', 'question': 'Does drift change when reports contain tables?',
            'reasoning': 'The authors say they did not test tables.', 'dimensions': [], 'suggestion': 'Add reports with tables.',
            'limitations': 'Only one paper looks at drift.', 'fact_ids': [c['limitation']]}


def fields(sheets):
    return [{field: i for (title, field), i in ids([s]).items()} for s in sheets]


def test_only_opportunities_the_facts_support_are_kept(tmp_path):
    lib = library(tmp_path)
    sheets = facts.papers(lib.core_artifacts(), lib.resolve_core_private_artifact)
    a, b, c = fields(sheets)
    response = {'opportunities': [
        divergence(a, b),
        divergence(a, b, title='A repeat of the first'),                                    # same facts: kept once
        divergence(a, b, fact_ids=[a['key_data'], 'P9.1']),                                  # no such fact
        divergence(a, a, fact_ids=[a['key_data'], a['key_results']], dimensions=[]),         # one paper only
        divergence(a, b, reasoning='One reports 0.05, the other 0.07.'),                      # 0.07 is in no fact
        divergence(a, b, title='Drift after 4,000 tokens', dimensions=[]),                    # 4,000 is in paper 3 only
        divergence(a, b, kind='condition_difference',                                       # 1200 is 1,200; 2 is a count
                   reasoning='The 1200 synthetic reports came out at 0.05, the real notes at 0.11, across 2 papers.'),
        dict(gap(c), reasoning='Drift starts after 4,000 tokens, and the authors did not test tables.')]}
    kept, dropped = finder.checked(response, sheets)
    assert [d['reason'] for d in dropped] == ['FACT_ID_UNKNOWN', 'ONE_PAPER_ONLY', 'NUMBER_NOT_IN_CITED_PAPERS',
                                              'NUMBER_NOT_IN_CITED_PAPERS']
    assert [k['kind'] for k in kept] == ['divergence', 'condition_difference', 'gap']
    # The gap cites only the limitation; 4,000 comes from the paper's results, which join its evidence.
    assert kept[2]['fact_ids'] == [c['limitation'], c['key_results']]
    assert [e['field'] for e in kept[2]['evidence']] == ['limitation', 'key_results']
    first = kept[0]
    assert [p['title'] for p in first['papers']] == ['Calibrating report classifiers on synthetic data', 'Calibration of clinical note classifiers']
    assert {e['quote'] for e in first['evidence'] if e['field'] == 'key_data'} == {'ECE fell to 0.05 after temperature scaling', 'ECE remained 0.11'}
    assert first['papers'][0]['library_files'] == [{'id': 'job-a/CORE_CARD', 'content_hash': 'job-a-CORE_CARD-sha'}]


def test_an_opportunity_keeps_its_fingerprint_when_the_papers_are_numbered_differently(tmp_path):
    lib = library(tmp_path)
    sheets = facts.papers(lib.core_artifacts(), lib.resolve_core_private_artifact)
    a, b, _ = fields(sheets)
    kept, _ = finder.checked({'opportunities': [divergence(a, b)]}, sheets)
    # A paper processed later sorts first: every paper gets the next number.
    more = library(tmp_path / 'later', {'job-0': PAPERS['job-c'].replace('Reading long reports', 'An earlier paper'), **PAPERS})
    renumbered = facts.papers(more.core_artifacts(), more.resolve_core_private_artifact)
    _, a2, b2, _ = fields(renumbered)
    assert a2 != a
    again, _ = finder.checked({'opportunities': [divergence(a2, b2)]}, renumbered)
    assert again[0]['fingerprint'] == kept[0]['fingerprint']


def test_the_readable_result_names_the_papers_and_quotes_them(tmp_path):
    lib = library(tmp_path)
    sheets = facts.papers(lib.core_artifacts(), lib.resolve_core_private_artifact)
    a, b, c = fields(sheets)
    kept, _ = finder.checked({'opportunities': [divergence(a, b), gap(c)]}, sheets)
    text = finder.markdown(kept, day='2026-11-18', paper_count=3, earlier=0, language=None)
    assert text.startswith('# 研究机会 · 2026-11-18\n\n从 3 篇论文中找到 2 条新的研究机会：分歧 1 条，缺口 1 条。\n')
    assert '## 1. 分歧：Two papers disagree' in text
    assert '- 方法（相同）：Both use temperature scaling.' in text
    assert '- 《Calibration of clinical note classifiers》· 关键数据（第 2 页，Results）：expected calibration error：0.11（900 notes） “ECE remained 0.11”' in text
    assert '## 2. 缺口：Reports with tables are untested' in text


# The run

SETTINGS = {'settings': {'preferences': {'language': 'zh-CN'}}, 'revision': 0}


class Model:
    """The settings service, with a stub model that answers each group with the prepared opportunities."""

    def __init__(self, answer, fail_group=None):
        self.answer, self.fail_group, self.prompts, self.snapshots = answer, fail_group, [], []

    def call(self, method, params):
        if method == 'settings.get_state':
            return copy.deepcopy(SETTINGS)
        if method == 'settings.external_sources_get':
            return {'literature': {'selected': []}}
        if method == 'settings.research_get':
            return {'config': {}}
        if method == 'settings.research_chat_freeze':
            self.snapshots.append(params['run_id'])
            return {'snapshot_id': str(len(self.snapshots)), 'profile_ref': params['profile_ref'], 'profile_kind': 'API',
                    'model_name': 'stub-chat', 'display_name': 'Stub chat'}
        if method == 'settings.research_chat_execute':
            self.prompts.append(params['prompt'])
            if self.fail_group == len(self.prompts):
                return {'status': 'FAILED', 'execution_receipt': {'status': 'FAILED'}}
            return {'status': 'PASS', 'requested_model': 'stub-chat', 'returned_model': 'stub-chat',
                    'execution_receipt': {'status': 'PASS', 'behavior_sha256': 'b', 'token_usage': {'input_tokens': 1},
                                          'physical_call_ids': ['call-' + str(len(self.prompts))]},
                    'response': self.answer(params['prompt'])}
        raise AssertionError(method)


@pytest.fixture
def research(tmp_path, monkeypatch):
    from memorive.research import runtime as research_runtime
    from memorive.research.briefing import jobs
    from memorive.research.reports import models as report_models
    monkeypatch.setattr(jobs, 'workspace_settings', lambda host: {'profile_ref': 'api:chat'})
    monkeypatch.setattr(report_models, 'catalog', lambda runtime, capability='CHAT': [{'profile_ref': 'api:chat', 'eligible': True}])
    lib = library(tmp_path)
    messages = []

    def make(answer, fail_group=None):
        r = research_runtime.ResearchRuntime.__new__(research_runtime.ResearchRuntime)
        r.root = tmp_path / 'WORKSPACE' / 'research'
        r.root.mkdir(parents=True, exist_ok=True)
        r.lock, r.stop, r.closed, r.current, r.thread, r._node_progress_live = threading.RLock(), threading.Event(), False, None, None, {}
        r.host = SimpleNamespace(service=Model(answer, fail_group), library=lib, notify_window=lambda name: None,
                                 messages=SimpleNamespace(post_event=lambda event: (messages.append(event), ['message-1'])[1]),
                                 profile_path=lambda area: tmp_path / area)
        r.opportunities = runs.Opportunities(r)
        return r

    def run(r, request_id, focus=''):
        item = r.opportunities.call('opportunities.find', {'request_id': request_id, 'focus': focus})
        r.thread.join(20)
        return r.call('research.get', {'run_id': item['run_id']})

    return SimpleNamespace(make=make, run=run, messages=messages, library=lib)


def answer_from_prompt(prompt):
    """Answer with the opportunities of the papers in this prompt, by their ids in it."""
    sheets = json.loads(prompt.split('Fact sheets:\n', 1)[1])
    by_title = {s['title']: {f['field']: f['id'] for f in s['facts']} for s in sheets}
    a = by_title.get('Calibrating report classifiers on synthetic data')
    b = by_title.get('Calibration of clinical note classifiers')
    c = by_title.get('Reading long reports')
    found = []
    if a and b:
        found.append(divergence(a, b))
        found.append(divergence(a, b, reasoning='One reports 0.05, the other 0.07.', title='An invented number'))
    if c:
        found.append(gap(c))
    return {'opportunities': found}


def test_a_run_finds_checks_saves_and_announces_the_opportunities(research):
    r = research.make(answer_from_prompt)
    state = research.run(r, 'first', focus='calibration')
    assert (state['status'], state['stage']) == ('SUCCEEDED', 'COMPLETE')
    result = state['result']
    assert [o['kind'] for o in result['opportunities']] == ['divergence', 'gap']
    assert result['dropped'] == [{'title': 'An invented number', 'reason': 'NUMBER_NOT_IN_CITED_PAPERS', 'group': 1}]
    assert (result['paper_count'], result['model_calls']) == (3, 1)
    prompt = r.host.service.prompts[0]
    assert 'calibration' in prompt and 'Reading long reports' in prompt
    library_row = json.loads((r.run_path(state['run_id']) / 'library.json').read_text(encoding='utf-8'))['row']
    assert (library_row['kind'], library_row['mode'], library_row['product_kind']) == ('研究机会', 'memorive', 'RESEARCH_OPPORTUNITIES')
    assert (r.run_path(state['run_id']) / 'result.md').read_text(encoding='utf8') == result['markdown']
    message = research.messages[-1]
    assert message['message_content']['message_key'] == 'opportunities.ready'
    assert message['safe_summary'].startswith('从 3 篇论文中找到 2 条新的研究机会。')
    from memorive.research.task_projection import bindings
    task = next(row for row in bindings(r) if row['run'] == state['run_id'])
    assert task['workflow_kind'] == 'RESEARCH_OPPORTUNITIES'
    assert [n['name'] for n in task['workflow_nodes']][2] == '寻找并核对研究机会'
    assert task['workflow_nodes'][2]['model'] == 'Stub chat'


def test_what_the_user_did_with_an_opportunity_survives_the_next_run(research):
    r = research.make(answer_from_prompt)
    research.run(r, 'first')
    state = r.opportunities.call('opportunities.state', {})
    assert [i['status'] for i in state['items']] == ['new', 'new'] and state['papers_since_last_run'] == 0
    divergence_item = next(i for i in state['items'] if i['kind'] == 'divergence')
    with pytest.raises(ValueError, match='OPPORTUNITIES_REVISION_CONFLICT'):
        r.opportunities.call('opportunities.mark', {'fingerprint': divergence_item['fingerprint'], 'status': 'dismissed',
                                                    'expected_revision': state['revision'] - 1})
    marked = r.opportunities.call('opportunities.mark', {'fingerprint': divergence_item['fingerprint'], 'status': 'dismissed',
                                                         'expected_revision': state['revision']})
    assert [i['status'] for i in marked['items']] == ['new', 'dismissed']
    research.run(r, 'second')
    # The second request names what was found before, the dismissed one included.
    known = json.loads(r.host.service.prompts[-1].split('earlier runs', 1)[1].split('\n', 2)[2].split('\n\nFact sheets', 1)[0])
    assert sorted((k['kind'], k['papers'] == ['P1', 'P2'] or k['papers'] == ['P3']) for k in known) == [('divergence', True), ('gap', True)]
    assert '之前找到的 2 条不再重复列出' in r.call('research.get', {'run_id': r.current})['result']['markdown']
    again = r.opportunities.call('opportunities.state', {})
    item = next(i for i in again['items'] if i['fingerprint'] == divergence_item['fingerprint'])
    assert item['status'] == 'dismissed' and item['first_found_at'] == divergence_item['first_found_at']
    assert len(again['items']) == 2
    # A dismissed divergence is not a tension for the weekly report.
    assert runs.report_rows(r.root) == []


def test_an_opportunity_whose_card_changed_is_marked_outdated(research):
    r = research.make(answer_from_prompt)
    research.run(r, 'first')
    research.library.records['job-c/CORE_CARD']['content_sha256'] = 'job-c-card-revised'
    state = r.opportunities.call('opportunities.state', {})
    assert {i['kind']: i['outdated'] for i in state['items']} == {'divergence': False, 'gap': True}
    assert state['papers_since_last_run'] == 1


def test_a_group_whose_request_fails_leaves_the_others_and_a_partial_result(research, monkeypatch):
    monkeypatch.setattr(finder.groups, '__defaults__', (2,))
    r = research.make(answer_from_prompt, fail_group=2)
    state = research.run(r, 'first')
    assert state['status'] == 'PARTIAL'
    assert len(state['result']['failed_groups']) == 1 and state['result']['failed_groups'][0]['error'] == 'CHAT_MODEL_EXECUTION_FAILED'
    assert len(r.host.service.snapshots) == len(set(r.host.service.snapshots)) == 2    # one snapshot per request


def test_without_a_usable_model_nothing_starts(research, monkeypatch):
    from memorive.research.reports import models as report_models
    monkeypatch.setattr(report_models, 'catalog', lambda runtime, capability='CHAT': [{'profile_ref': 'api:chat', 'eligible': False}])
    r = research.make(answer_from_prompt)
    with pytest.raises(ValueError, match='OPPORTUNITIES_MODEL_REQUIRED'):
        r.opportunities.call('opportunities.find', {'request_id': 'first'})
    assert not (r.root / 'runs').exists()


def test_the_weekly_report_lists_a_divergence_under_tensions_between_sources(research):
    r = research.make(answer_from_prompt)
    research.run(r, 'first')
    rows = runs.report_rows(r.root)
    assert [row['opportunity_kind'] for row in rows] == ['divergence']
    for row in rows:
        row['updated_at'] = '2026-08-12T03:00:00Z'
    from memorive.research.reports.periodic import render
    params = {'report_window': {'period': 'weekly', 'start': '2026-08-10T00:00:00+08:00', 'end': '2026-08-17T00:00:00+08:00'},
              'material_progress': rows, 'report_cutoff': '2026-08-17T00:30:00+08:00', 'language_context': None}
    with patch('memorive.research.reports.periodic.write', lambda path, value: None):
        result = render(SimpleNamespace(run_path=lambda run_id: Path('.'), update_state=lambda *a, **k: None), 'run-1', params)
    tension = next(s for s in result['digest']['sections'] if s['title'] == '明确来源的潜在张力')
    assert [i['text'] for i in tension['items']] == [
        '分歧：Two papers disagree on how far temperature scaling lowers calibration error'
        '（《Calibrating report classifiers on synthetic data》、《Calibration of clinical note classifiers》）']
    assert '## 明确来源的潜在张力' in result['markdown'] or '潜在张力' in result['markdown']
    assert 'Two papers disagree' in result['markdown']
