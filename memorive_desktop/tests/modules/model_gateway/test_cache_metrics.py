from memorive.models.gateway.cache_metrics import cache_metrics
from memorive.models.gateway.prompt_cache import cache_usage


def test_output_and_reasoning_are_not_added_twice():
    result = cache_metrics({'input_tokens': 100, 'cached_input_tokens': 80,
                            'output_tokens': 50, 'reasoning_tokens': 30}, duration_ms=2000)
    assert result['visible_output_tokens'] == 20
    assert result['seconds_per_1000_output_tokens'] == 40
    assert result['seconds_per_1000_visible_output_tokens'] == 100
    assert result['cost_basis'] == 'UNKNOWN'
    assert result['cost_per_1000_output_tokens'] is None


def test_zero_unknown_and_conflicting_cache_counts_are_distinct():
    assert cache_usage({})['status'] == 'UNKNOWN'
    assert cache_usage({'cached_input_tokens': 0})['status'] == 'MISS'
    assert cache_usage({'cached_input_tokens': 1, 'prompt_cache_hit_tokens': 2})['status'] == 'INCONSISTENT'
    assert cache_usage({'cachedContentTokenCount': 5})['read_tokens'] == 5
    assert cache_usage({'cache_read_input_tokens': 5, 'input_tokens': 1})['status'] == 'HIT'


def test_money_basis_is_explicit_and_actual_does_not_replace_unknown_with_zero():
    result = cache_metrics({'output_tokens': 100}, estimated_cost=2)
    assert result['cost_basis'] == 'ESTIMATED'
    assert result['cost_per_1000_output_tokens'] == 20
    assert result['visible_output_tokens'] is None
    result = cache_metrics({'output_tokens': 0}, duration_ms=0)
    assert result['seconds_per_1000_output_tokens'] is None


def test_provider_aliases_reach_existing_accounting_kernel():
    from memorive.models.gateway.accounting.contracts import UsageObservation
    row = UsageObservation.from_provider_usage({'prompt_tokens': 100, 'completion_tokens': 20,
                                                'prompt_cache_hit_tokens': 80})
    assert row.cache_read_input_tokens == 80
    assert row.component_counts()['input'] == 20
