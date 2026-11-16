"""sample feedback regressions; real cache/controller, no inference."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_external_reference_integration import controller
from memorive_settings.external_sources import role_references
from memorive.models.validation.runner import LiveModelValidationRunner

ROOT = Path(__file__).resolve().parents[1]


def row(model, value, source='epoch-ai', version='v1'):
    return dict(source_id=source, upstream_source=source, model_id=model, model_name=model,
                metrics={'eci':value}, version=version)

@pytest.mark.parametrize('name', ['[本地] qwen3.8:27b-q4_K_M', 'qwen3.8:27b-q4_K_M', 'Qwen/Qwen3.8-27B'])
def test_local_base_model_alias_only_for_external_reference(name):
    rows=[row('qwen3.8-27b_high', 120), row('qwen3.8-35b_high',130), row('qwen3.7-27b_high',110)]
    result=role_references('ANALYSIS_PRIMARY', {'kind':'LOCAL','model_name':name}, rows)
    assert result['references'][0]['metrics']['eci']==120
    assert result['references'][0]['match_kind']=='LOCAL_BASE_MODEL_REFERENCE'
    assert result['exact_execution_profile_assessed'] is False

def test_percentile_uses_source_metric_version_and_unique_models():
    rows=[row('a_high',10),row('a_max',10),row('b_high',20),row('c_high',30),
          row('b_high',200,'elsewhere'),row('z_high',999,version='v2')]
    result=role_references('ANALYSIS_PRIMARY', {'model_name':'b','thinking_mode':'high'}, rows)
    selected=next(r for r in result['references'] if r['source_id']=='epoch-ai')
    display=selected['reference_percentiles']['eci']
    assert display['value']==50 and display['population_count']==3
    assert selected['metrics']['eci']==20
    assert display['method']=='SOURCE_METRIC_MIDRANK_PERCENTILE_V1'

def test_missing_cohort_or_ambiguous_local_version_is_not_zero():
    result=role_references('ANALYSIS_PRIMARY', {'model_name':'only'}, [row('only',20)])
    assert result['references'][0]['reference_percentiles']['eci']['value'] is None
    assert not role_references('ANALYSIS_PRIMARY', {'kind':'LOCAL','model_name':'qwen3.8:27b-q4_K_M'},
        [row('qwen3.8-35b_high', 130),row('qwen3.8-max_high',150)])['references']


def test_forwarded_score_is_displayable_but_not_an_independent_vote():
    rows=[row('gpt-5.6-sol',80,'artificial-analysis'),row('other',60,'artificial-analysis'),
          {**row('gpt-5.6-sol',80,'openrouter'),'upstream_source':'artificial-analysis'},
          {**row('other',60,'openrouter'),'upstream_source':'artificial-analysis'}]
    result=role_references('ANALYSIS_PRIMARY',{'model_name':'gpt-5.6-sol'},rows)
    assert sum('eci' in r['metrics'] for r in result['references'])==1
    assert all(r['display_metrics']['eci']==80 for r in result['references'])
    assert len(result['references'])==2

