"""Translate saved product options at the provider boundary; never change profiles."""
from __future__ import annotations
import hashlib
import re
from copy import deepcopy
from typing import Any, Mapping

EFFORTS = frozenset({'low','medium','high','xhigh','max'})

# Reuse the provider-neutral capability registry. The mapper contains no model
# or provider names; new interfaces describe controls as data, not new branches.
_TIER_PLANS = {'economy':'ECONOMY', 'standard':'STANDARD', 'high-quality':'HIGH_QUALITY', 'maximum':'MAXIMUM'}
_PLANS = tuple(_TIER_PLANS.values())

def _control_points(control):
    mode = control.get('thinking_mode', 'UNKNOWN')
    values = control.get('effort_values', [])
    if mode not in {'ALWAYS','OPTIONAL','NONE'} or not isinstance(values,list):
        raise ValueError('API_REASONING_CAPABILITY_INVALID')
    if any(not isinstance(v,str) or not v for v in values) or len(values) != len(set(values)):
        raise ValueError('API_REASONING_CAPABILITY_INVALID')
    points = [{'thinking':False}] if mode == 'OPTIONAL' else []
    if mode != 'NONE':
        points.extend({'thinking':True, 'effort':value} for value in values)
        if not values:
            budgets = control.get('recommended_budget_tokens', [])
            if budgets:
                if any(type(v) is not int or v<=0 for v in budgets) or budgets!=sorted(set(budgets)):
                    raise ValueError('API_REASONING_CAPABILITY_INVALID')
                points.extend({'thinking':True,'budget_tokens':v} for v in budgets)
            else:
                points.append({'thinking':True})
    return points or [{}]

def project_reasoning_control(control):
    value=deepcopy(control); points=_control_points(value)
    # Normal presets express increasing investment. Duplicate outcomes are
    # collapsed by the UI. Never invent a provider enum or a numeric token cap.
    value['points']=points
    value['plan_indices']={plan:(index*(len(points)-1)+2)//3 for index,plan in enumerate(_PLANS)}
    return value

def reasoning_profiles() -> dict[str, Any]:
    from model_gateway.capabilities import DEFAULT_REGISTRY_PATH
    import json
    raw=json.loads(DEFAULT_REGISTRY_PATH.read_text('utf8'))
    found={}
    for provider, models in raw.get('providers',{}).items():
        for name, entry in models.items():
            control=entry.get('capabilities',{}).get('reasoning_control')
            if control:
                found.setdefault(name,[]).append({'provider':provider,**project_reasoning_control(control)})
    return found

def reasoning_profile(model, url, protocol):
    from urllib.parse import urlsplit
    host=(urlsplit(url).hostname or '').lower()
    matches=[v for v in reasoning_profiles().get(model,[]) if host in v.get('origins',[]) and protocol in v.get('protocols',[])]
    if len(matches)>1:raise ValueError('API_REASONING_CAPABILITY_AMBIGUOUS')
    return matches[0] if matches else None

def _wire_set(body, path, value):
    if path not in {'thinking.type','thinking.budget_tokens','reasoning_effort','output_config.effort','generationConfig.thinkingConfig.thinkingBudget'}:
        raise ValueError('API_REASONING_WIRE_FIELD_INVALID')
    keys=path.split('.');target=body
    for key in keys[:-1]:target=target.setdefault(key,{})
    target[keys[-1]]=value

def resolve_reasoning_options(control, config):
    if control is None:return None
    control=project_reasoning_control(control)
    tier=str(config.get('tier') or '').strip()
    plan=_TIER_PLANS.get(tier)
    explicit=config.get('reasoning_effort')
    budget=config.get('thinking_budget_tokens')
    mode=control['thinking_mode']
    if plan:
        point=dict(control['points'][control['plan_indices'][plan]])
    else:
        point={'thinking': True if mode=='ALWAYS' else bool(config.get('thinking', control.get('default_thinking',True)))} if mode!='NONE' else {}
        if control['effort_values'] and (tier in control['effort_values'] or tier in EFFORTS):point['effort']=tier
    if explicit is not None:point['effort']=explicit
    if budget is not None:point['budget_tokens']=budget
    if point.get('effort') is not None and point['effort'] not in control['effort_values']:
        raise ValueError('API_REASONING_EFFORT_UNSUPPORTED')
    if 'budget_tokens' in point:
        limits=control.get('budget_range') or {}
        if type(point['budget_tokens']) is not int or not limits or not limits['min']<=point['budget_tokens']<=limits['max']:
            raise ValueError('API_REASONING_BUDGET_UNSUPPORTED')
    # An explicit advanced effort enables an optional reasoning mode. A stale
    # false checkbox must not silently discard a selected effort.
    if point.get('effort') is not None or 'budget_tokens' in point:point['thinking']=True
    result={}
    if control.get('thinking_wire') and mode!='NONE':
        _wire_set(result,control['thinking_wire'],control['thinking_values']['on' if point.get('thinking') else 'off'])
    if point.get('thinking') and 'effort' in point:_wire_set(result,control['effort_wire'],point['effort'])
    if point.get('thinking') and 'budget_tokens' in point:_wire_set(result,control['budget_wire'],point['budget_tokens'])
    return result


def apply_reasoning_options(body, options):
    """Merge only declared controls, preserving the adapter's schema/output fields."""
    for key, value in options.items():
        if isinstance(value, dict) and isinstance(body.get(key), dict):
            apply_reasoning_options(body[key], value)
        else:
            body[key] = deepcopy(value)

def anthropic_options(model: str, config: Mapping[str, Any], output_limit: int) -> dict[str, Any]:
    options: dict[str, Any] = {}
    enabled = config.get('thinking')
    adaptive = config.get('adaptive_thinking')
    effort = config.get('reasoning_effort')
    if effort is None and enabled is True:
        effort = config.get('tier') or None
    if effort is not None:
        if effort not in EFFORTS:
            raise ValueError('API_REASONING_EFFORT_INVALID')
        options['output_config'] = {'effort':effort}
    if adaptive is True:
        options['thinking'] = {'type':'adaptive'}
    elif enabled is True:
        # These named product profiles have a known adaptive contract. Unknown
        # profiles require an explicit adaptive flag or a manual token budget.
        known_adaptive = bool(re.match(r'^claude-(?:sonnet-5|opus-4[.-]8)(?:$|-)',model))
        budget = config.get('thinking_budget_tokens')
        if known_adaptive and adaptive is not False:
            options['thinking'] = {'type':'adaptive'}
        elif isinstance(budget,int) and not isinstance(budget,bool) and 1024 <= budget < output_limit:
            options['thinking'] = {'type':'enabled','budget_tokens':budget}
        else:
            raise ValueError('API_THINKING_CONFIGURATION_REQUIRED')
    elif enabled is False:
        options['thinking'] = {'type':'disabled'}
    # Sampling is optional. Do not insert temperature=0: current adaptive models
    # reject it, and provider defaults remain valid for older profiles as well.
    return options

ERROR_TYPES = frozenset({'invalid_request_error','authentication_error','permission_error',
    'not_found_error','rate_limit_error','request_too_large','api_error','overloaded_error',
    'invalid_api_key','insufficient_quota','context_length_exceeded','invalid_parameter'})
ERROR_FIELDS = frozenset({'model','max_tokens','max_completion_tokens','temperature','top_p',
    'top_k','thinking','thinking.budget_tokens','budget_tokens','output_config.effort',
    'reasoning_effort','response_format','messages','stream'})

def safe_error_details(value: Mapping[str, Any]) -> dict[str, Any]:
    """Retain actionable categories and hashes, never provider-echoed user text."""
    result: dict[str, Any] = {}
    request_id = value.get('request_id')
    if isinstance(request_id,str) and request_id:
        result['provider_request_id_sha256'] = hashlib.sha256(request_id.encode()).hexdigest()
    error=value.get('error')
    if not isinstance(error,Mapping):return result
    for key in ('type','code'):
        code=error.get(key)
        if isinstance(code,str) and code in ERROR_TYPES:result['provider_error_'+key]=code
    field=error.get('param') or error.get('field')
    if isinstance(field,str) and field in ERROR_FIELDS:result['provider_error_field']=field
    message=error.get('message')
    if isinstance(message,str):
        result['provider_error_message_sha256']=hashlib.sha256(message.encode()).hexdigest()
        if 'provider_error_field' not in result:
            for field in sorted(ERROR_FIELDS,key=len,reverse=True):
                if re.search(r'(?<![A-Za-z0-9_])'+re.escape(field)+r'(?![A-Za-z0-9_])',message):
                    result['provider_error_field']=field
                    break
    return result
