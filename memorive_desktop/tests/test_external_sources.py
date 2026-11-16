from copy import deepcopy
import io
import json
import zipfile

import pytest

from memorive_settings.external_sources import (
    ExternalDataSources, default_sources, normalize_payload, role_references,
    validate_source,
)
from memorive.models.validation import HttpResponse
from legacy_aa_fixture import aa_source, configure_aa


def test_aa_null_is_not_zero_and_external_scores_never_qualify():
    source = aa_source()
    rows = normalize_payload(source, {'intelligence_index_version': 4.1, 'data': [
        {'id': 'stable-1', 'slug': 'model-1', 'name': 'Model 1', 'evaluations': {
            'artificial_analysis_intelligence_index': 82, 'aa_lcr': None, 'ifbench': 0.72}}
    ]})
    assert rows[0]['metrics']['aa_lcr'] is None
    view = role_references('ANALYSIS_PRIMARY', {'model_name': 'model-1'}, rows)
    assert view['internal_exam_score'] is None
    assert view['qualification_status'] == 'NOT_ASSESSED'
    assert view['reference_only'] is True


def test_openrouter_aa_lineage_is_deduplicated_not_independent_vote():
    aa = normalize_payload(aa_source(), {'data': [
        {'id': 'a', 'slug': 'same', 'evaluations': {'artificial_analysis_intelligence_index': 82}}
    ]})
    forwarded = normalize_payload(default_sources()[2], {'data': [
        {'source': 'artificial-analysis', 'model_permaslug': 'same', 'intelligence_index': 82},
        {'source': 'openrouter', 'model_permaslug': 'same', 'benchmark_type': 'gpqa_diamond', 'accuracy': .72}
    ], 'meta': {'as_of': '2026-09-04', 'version': 'v1'}})
    result = role_references('ANALYSIS_PRIMARY', {'model_name': 'same'}, aa + forwarded)
    assert sum('artificial_analysis_intelligence_index' in x['metrics'] for x in result['references']) == 1
    assert any(x['upstream_source'] == 'openrouter' for x in result['references'])


def test_epoch_zip_never_extracts_files_and_preserves_eci_scale():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('epoch_capabilities_index.csv', 'Model version,ECI Score,Confidence,Display name\nm1,158,Speculative,Model 1\n')
    rows = normalize_payload(default_sources()[1], buffer.getvalue())
    assert rows[0]['metrics']['eci'] == 158
    assert rows[0]['scale'] == 'ECI_UNBOUNDED'
    assert rows[0]['confidence_interval'] is None
    assert rows[0]['source_auxiliary_confidence_label'] == 'Speculative'
    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, 'w') as z:
        z.writestr('../epoch_capabilities_index.csv', 'x')
    with pytest.raises(ValueError):
        normalize_payload(default_sources()[1], malicious.getvalue())


@pytest.mark.parametrize('url', ['http://example.com/api', 'https://127.0.0.1/api', 'https://u:p@example.com/api', 'https://example.com/?api_key=secret'])
def test_custom_endpoint_safety(url):
    source = default_sources()[0]
    source['endpoint'] = url
    with pytest.raises(ValueError):
        validate_source(source)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []
    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_refresh_cache_failure_and_replacement_credential_binding(tmp_path):
    transport = FakeTransport(HttpResponse(200, json.dumps({'data': [{'id': 'm1', 'slug': 'm1', 'evaluations': {'aa_lcr': 84}}]}).encode()))
    service = ExternalDataSources(tmp_path, transport=transport, environment={'ARTIFICIAL_ANALYSIS_API_KEY': 'test-only'})
    configure_aa(service)
    state = service.get_state()
    assert transport.calls == []
    result = service.refresh('artificial-analysis', expected_revision=state['revision'])
    assert result['status'] == 'PASS'
    assert result['external_model_calls'] == 0
    assert 'test-only' not in json.dumps(service.get_state())
    transport.response = HttpResponse(401, b'{"authorization":"do not reflect server errors"}')
    fail = service.refresh('artificial-analysis', expected_revision=state['revision'])
    assert fail['status'] == 'ERROR'
    assert service.get_state()['sources'][0]['cached_model_count'] == 1
    row = deepcopy(state['sources'][0]['config'])
    row['endpoint'] = 'https://replacement.example/api'
    with pytest.raises(ValueError, match='CREDENTIAL_ORIGIN'):
        service.save(row, expected_revision=state['revision'])
    row['credential_env'] = ''
    row['credential_origin'] = ''
    saved = service.save(row, expected_revision=state['revision'])
    assert saved['revision'] == state['revision'] + 1
    assert service.get_state()['sources'][0]['cached_model_count'] == 0


def test_custom_json_and_local_model_never_inherit_exact_cloud_score():
    source = default_sources()[0]
    source.update(format='REFERENCE_JSON_V1', endpoint='https://custom.example/reference', credential_env='', credential_origin='')
    rows = normalize_payload(source, {'schema_version': 'ExternalModelReference-v1', 'data': [
        {'model_id': 'cloud-qwen', 'model_name': 'qwen', 'metrics': {'aa_lcr': 91}, 'version': 'v1'}]})
    view = role_references('CARD_DISTILLER', {'kind': 'LOCAL', 'model_name': 'qwen:27b-q4', 'family': 'qwen'}, rows)
    assert view['references'] == []
    assert view['assessment_status'] == 'NO_MATCH'


def test_stale_conflict_and_tampered_cache_fail_closed(tmp_path):
    service = ExternalDataSources(tmp_path)
    source = default_sources()[2]
    service.save(source, expected_revision=0)
    with pytest.raises(ValueError, match='REVISION'):
        service.save(source, expected_revision=0)
