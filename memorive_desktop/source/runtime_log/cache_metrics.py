"""Evidence-only cache and throughput projection; pricing remains owned by MODEL_GATEWAY."""
from __future__ import annotations

import math


def _count(value):
    return value if type(value) is int and value >= 0 else None


def cache_metrics(usage, *, duration_ms=None, estimated_cost=None, actual_cost=None):
    """Unknown observations stay null; reasoning is a subset of output, not extra output."""
    from model_gateway.prompt_cache import cache_usage

    usage = usage or {}
    cache = cache_usage(usage)
    input_tokens = _count(usage.get('prompt_tokens', usage.get('input_tokens')))
    output = _count(usage.get('completion_tokens', usage.get('output_tokens')))
    details = usage.get('completion_tokens_details') or usage.get('output_tokens_details') or {}
    reasoning = _count(details.get('reasoning_tokens', usage.get('reasoning_output_tokens', usage.get('reasoning_tokens'))))
    visible = output - reasoning if output is not None and reasoning is not None and reasoning <= output else None
    elapsed = duration_ms if type(duration_ms) in (int, float) and math.isfinite(duration_ms) and duration_ms >= 0 else None
    cost = actual_cost if actual_cost is not None else estimated_cost
    valid_cost = type(cost) in (int, float) and math.isfinite(cost) and cost >= 0
    return {
        'schema_version': 'CacheMetrics-v1', 'input_tokens': input_tokens,
        'cache': cache, 'output_tokens': output, 'reasoning_tokens': reasoning,
        'visible_output_tokens': visible, 'duration_ms': elapsed,
        'seconds_per_1000_output_tokens': elapsed / output if elapsed is not None and output else None,
        'seconds_per_1000_visible_output_tokens': elapsed / visible if elapsed is not None and visible else None,
        'cost_per_1000_output_tokens': cost * 1000 / output if valid_cost and output else None,
        'cost_basis': ('ACTUAL' if actual_cost is not None else 'ESTIMATED') if valid_cost else 'UNKNOWN',
        'reasoning_is_output_subset': True,
        'comparison_limit': 'Compare equivalent completed scope and quality; normalized output is not proof of cache causation.',
    }
