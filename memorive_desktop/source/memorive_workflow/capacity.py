from __future__ import annotations
from typing import Any, Mapping
from model_gateway.resource_limits import describe_capacity


def context_pack_budget(
    descriptor: Mapping[str, Any], *, prompt_reserve_tokens: int = 0,
    candidate_estimated_tokens: int | None = None,
    prompt_measurement_source: str = 'NOT_MEASURED_PROVIDER_CHECK_REQUIRED',
) -> dict[str, Any]:
    """No fixed 4096/16384 reserve or 80% multiplier.

    The inherited retrieval estimator is planning evidence, not an exact
    tokenizer. Unknown capacity is not an invented product ceiling. Reasoning
    tokens are already part of the provider output allowance.
    """
    effective = descriptor.get('effective_capacity') or {}
    capacity = effective.get('input_tokens')
    shared = effective.get('shared_context_tokens')
    output = effective.get('output_tokens')
    output_reserve = output if shared is not None and output is not None else 0
    limits = []
    if isinstance(capacity, int) and capacity > 0:
        limits.append(capacity - prompt_reserve_tokens)
    if isinstance(shared, int) and shared > 0:
        limits.append(shared - prompt_reserve_tokens - output_reserve)
    available = min(limits) if limits else None
    assessment = 'ESTIMATED_PROVIDER_CHECK_REQUIRED'
    if available is None or available <= 0:
        # Without a separable input/output allowance, do not create a one-token
        # gate. Native model limits remain authoritative and are receipt-bound.
        available = candidate_estimated_tokens
        assessment = 'NOT_ASSESSED'
    if available == 0 and candidate_estimated_tokens == 0:
        available = 1  # nonzero builder representation of an EMPTY allocation
    if not isinstance(available, int) or available <= 0:
        raise ValueError('CONTEXT_PACK_CANDIDATE_MEASUREMENT_REQUIRED')
    return {
        'schema_version': 'DesktopContextPackBudget-v1',
        'token_hard_limit': available,
        'model_input_capacity_tokens': capacity, 'output_reserve_tokens': output_reserve,
        'prompt_reserve_tokens': prompt_reserve_tokens, 'reasoning_reserve_tokens': 0,
        'safety_margin': 1.0, 'capacity_assessment': assessment,
        'prompt_measurement_source': prompt_measurement_source,
        'capacity_source': descriptor.get('capacity_source'),
        'effective_capacity_source': descriptor.get('effective_capacity_source'),
    }


__all__ = ['context_pack_budget', 'describe_capacity']
