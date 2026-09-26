"""Resource policy, not workflow policy. No empirical number is a rejection gate.

Declared provider/service/runtime constraints are intersected, never doubled after
failure. Historical caller guesses remain receipt metadata only. Unknown limits
remain unknown; CLI and Ollama may manage them natively.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

EXAM_PLANNING_REFERENCE_TOKENS = 173030  # observed Opus peak 17303 * user-authorized 10

def _minimum(values):
    values = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return min(values) if values else None

def _values(containers, keys):
    return [row.get(key) for row in containers if isinstance(row, Mapping) for key in keys]

def _registry_entry(model_name: str):
    from .capabilities import DEFAULT_REGISTRY_PATH
    try:
        path = Path(DEFAULT_REGISTRY_PATH)
        data = path.read_bytes()
        registry = json.loads(data)
        matches = [(provider, rows[model_name]) for provider, rows in registry.get('providers', {}).items()
                   if isinstance(rows, Mapping) and isinstance(rows.get(model_name), Mapping)]
        if len(matches) == 1:
            provider, row = matches[0]
            return dict(row), f'm9_gateway.model_capabilities:{provider}:sha256:{hashlib.sha256(data).hexdigest().upper()}'
    except (OSError, ValueError, TypeError):
        pass
    # Official normal Messages limits; batch-beta capacity is deliberately absent.
    if model_name in {'claude-opus-5', 'claude-sonnet-5', 'claude-fable-5-1'}:
        return {'context_window_tokens': 1000000, 'max_output_tokens': 128000,
                'capabilities': {'streaming': True}}, (
            'OFFICIAL_2026-09-04:https://platform.claude.com/docs/en/models/overview')
    return {}, None

def describe_capacity(*, profile_kind, execution_profile, node_requested_output_tokens=None):
    execution = execution_profile if isinstance(execution_profile, Mapping) else {}
    service = execution.get('service') if isinstance(execution.get('service'), Mapping) else {}
    model = execution.get('model') if isinstance(execution.get('model'), Mapping) else {}
    rows = (model, service)
    name = str(model.get('model_name') or '')
    registry, registry_source = _registry_entry(name)
    output_keys = ('max_output_tokens', 'output_token_limit', 'max_completion_tokens',
                   'provider_max_output_tokens')
    input_keys = ('max_input_tokens', 'context_window_tokens', 'context_length')
    saved_input = _minimum(_values(rows, input_keys))
    saved_output = _minimum(_values(rows, output_keys))
    provider_input = _minimum([saved_input, *_values((registry,), input_keys)])
    provider_output = _minimum([saved_output, *_values((registry,), output_keys)])
    # DeepSeek's legacy registry field is labelled max_input, but its source says
    # shared context. Do not treat a general max_input value as shared context.
    shared = _minimum(_values((*rows, registry), ('context_window_tokens', 'context_length')))
    if name in {'deepseek-v4-flash', 'deepseek-v4-pro'} and registry:
        shared = _minimum([shared, registry.get('max_input_tokens')])
    machine_input = _minimum(_values(rows, ('machine_safe_context_tokens', 'runtime_context_tokens', 'loaded_context_tokens')))
    machine_output = _minimum(_values(rows, ('machine_safe_output_tokens', 'runtime_output_tokens')))
    user_output = _minimum(_values(rows, ('user_max_output_tokens',)))
    effective_input = _minimum([provider_input, machine_input])
    effective_shared = _minimum([shared, machine_input])
    if profile_kind == 'LOCAL' and machine_input is None:
        # A model's theoretical context is not a measured/loaded local context.
        effective_input = effective_shared = None
    effective_output = _minimum([provider_output, machine_output, user_output, effective_shared])
    source = 'SAVED_EXECUTION_PROFILE' if saved_input is not None or saved_output is not None else registry_source
    return {
        'schema_version': 'P08Build087CapacityDescriptor-v1', 'model_name': name or None,
        'profile_kind': profile_kind, 'provider_input_capacity_tokens': provider_input,
        'provider_output_capacity_tokens': provider_output,
        'node_requested_output_tokens': node_requested_output_tokens,
        'machine_safe_input_tokens': machine_input, 'machine_safe_output_tokens': machine_output,
        'user_output_limit_tokens': user_output,
        'effective_capacity': {'input_tokens': effective_input, 'output_tokens': effective_output,
                               'shared_context_tokens': effective_shared},
        'capacity_source': source or 'PROVIDER_OR_RUNTIME_MANAGED_CAPACITY_UNKNOWN',
        'registry_source': registry_source,
        'effective_capacity_source': 'INTERSECTION_OF_KNOWN_CONSTRAINTS',
        'product_hard_limit_added': False,
    }

def resolve_call_limits(*, profile_kind, service, model, requested_output_tokens=None, input_tokens=None):
    capacity = describe_capacity(profile_kind=profile_kind,
        execution_profile={'service': service, 'model': model},
        node_requested_output_tokens=requested_output_tokens)
    effective = capacity['effective_capacity']
    output = effective['output_tokens']
    shared = effective['shared_context_tokens']
    output_limit_mode = model.get('max_output_tokens_mode')
    if output_limit_mode == 'EXPLICIT':
        # An explicit workflow limit is a wire-level upper bound.  Provider
        # capacity describes what the model can support; it must never enlarge
        # the caller's requested response budget.
        output = _minimum([output, requested_output_tokens])
    input_known = isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0
    if input_known and shared is not None:
        remaining = shared - input_tokens
        if remaining <= 0:
            raise ValueError('MODEL_CONTEXT_CAPACITY_EXHAUSTED')
        output = _minimum([output, remaining])
    if profile_kind == 'CLI':
        output = None  # CLI owns its token budget; do not claim a parameter was sent.
    # A context window is not an output allowance. For an unknown local model
    # with only num_ctx available, let Ollama determine the remaining output.
    if profile_kind == 'LOCAL' and not any(capacity[key] for key in (
            'provider_output_capacity_tokens', 'machine_safe_output_tokens', 'user_output_limit_tokens')):
        output = None
    timeout = _minimum(_values((model, service), ('execution_timeout_seconds',)))
    return {'max_output_tokens': output, 'timeout_seconds': timeout, 'capacity': capacity,
            'input_tokens': input_tokens if input_known else None,
            'context_fit_assessment': 'INPUT_COUNT_KNOWN' if input_known else 'PROVIDER_CHECK_REQUIRED',
            'legacy_requested_output_tokens': requested_output_tokens,
            'exam_planning_reference_tokens': EXAM_PLANNING_REFERENCE_TOKENS,
            'planning_reference_is_hard_limit': False, 'failure_doubling_enabled': False}
