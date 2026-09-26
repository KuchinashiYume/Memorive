import pytest

from common import OPERATIONS, build_suite_catalog, validate_command


def test_research_qa_sample_is_zero_model_optional_extension():
    operation = OPERATIONS['research.qa.simulate']
    assert operation['model'] is False
    assert operation['extension'] is True
    value = validate_command({
        'request_id': 'diagnostic-research-qa-1',
        'operation': 'research.qa.simulate',
        'params': {
            'question': '为什么重要？',
            'answer': '用于验证临时研究问答卡片。',
            'citations': 'Demo 2026',
        },
        'allow_model_calls': False,
    })
    assert value['operation'] == 'research.qa.simulate'


def test_research_qa_rejects_empty_question_or_answer():
    for field in ('question', 'answer'):
        params = {'question': '问题', 'answer': '答案', 'citations': ''}
        params[field] = '   '
        with pytest.raises(ValueError, match='RESEARCH_QA_INVALID'):
            validate_command({'request_id': 'diagnostic-invalid-' + field,
                'operation': 'research.qa.simulate', 'params': params,
                'allow_model_calls': False})


def test_current_release_keeps_core_suite_ready_and_marks_qa_scenario_partial():
    current_operations = [name for name in OPERATIONS if name != 'research.qa.simulate']
    catalog = {row['preset_id']: row for row in build_suite_catalog(current_operations, connected=True)}
    assert catalog['full_safe']['status'] == 'READY'
    assert catalog['full_safe']['available_case_count'] == catalog['full_safe']['case_count']
    assert catalog['diagnostic_scenarios']['status'] == 'PARTIAL'
    assert catalog['diagnostic_scenarios']['available_case_count'] == 5
    assert catalog['diagnostic_scenarios']['case_count'] == 6
    assert catalog['diagnostic_scenarios']['missing_operations'] == ['research.qa.simulate']
