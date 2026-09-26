"""Non-promotional reference quotes. Never used to estimate or settle real calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import re
import time

from .contracts import canonical_sha256

POLICY = 'CATALOG_MATCHED_ENDPOINT_NO_PROMOTION_V2'
COMPATIBLE_POLICIES = {POLICY, 'CATALOG_MATCHED_ENDPOINT_NO_PROMOTION_V1'}
CATALOG_URL = 'https://openrouter.ai/api/v1/models'
_MODEL_ID = re.compile(r'~?[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}/[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}')


def endpoint_url(model_id):
    if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
        raise ValueError('REFERENCE_PRICE_MODEL_ID_INVALID')
    return CATALOG_URL + '/' + model_id + '/endpoints'


def is_endpoint_url(url):
    prefix = CATALOG_URL + '/'
    return isinstance(url, str) and url.startswith(prefix) and url.endswith('/endpoints') and bool(
        _MODEL_ID.fullmatch(url[len(prefix):-len('/endpoints')]))


def _decimal(value):
    if value is None or isinstance(value, bool):
        raise ValueError('REFERENCE_PRICE_NUMBER_INVALID')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('REFERENCE_PRICE_NUMBER_INVALID') from error
    if not result.is_finite() or not math.isfinite(float(result)):
        raise ValueError('REFERENCE_PRICE_NUMBER_INVALID')
    return result


def quote_binding(pricing):
    # Canonical decimal strings allow a JSON number/string round trip without
    # losing the binding between the catalog and endpoint verification.
    return canonical_sha256({k:str(_decimal(pricing.get(k)).normalize()) for k in ('prompt','completion')})


def regular_price(model_id, pricing, payload, *, canonical_model_id=None):
    result = {'policy':POLICY, 'status':'UNVERIFIED', 'model_id':model_id}
    try:
        endpoint_url(model_id)
        if canonical_model_id:
            endpoint_url(canonical_model_id)
        current = tuple(_decimal(pricing.get(k)) for k in ('prompt','completion'))
        if any(n < 0 for n in current):
            raise ValueError('REFERENCE_PRICE_NUMBER_INVALID')
        result['catalog_quote_sha256'] = quote_binding(pricing)
        data = payload.get('data') if isinstance(payload, dict) else None
        accepted_ids = {model_id}
        if canonical_model_id:
            accepted_ids.add(canonical_model_id)
        returned_model_id = data.get('id') if isinstance(data, dict) else None
        preview_successor = model_id[:-len('-preview')] if model_id.endswith('-preview') else None
        exact_preview_redirect = returned_model_id == preview_successor
        if (not isinstance(data, dict) or
                (returned_model_id not in accepted_ids and not exact_preview_redirect) or
                not isinstance(data.get('endpoints'), list)):
            raise ValueError('REFERENCE_PRICE_MODEL_MISMATCH')
        # The public free router is itself the exact SKU and its catalog quote
        # is permanently zero. Its endpoint response lists routed models, so a
        # provider quote cannot equal the router's own (0, 0) price.
        if model_id == 'openrouter/free' and current == (Decimal(0), Decimal(0)):
            result.update(status='VERIFIED', prompt=0.0, completion=0.0,
                          endpoints=[], selected_endpoint=None,
                          selection='EXPLICIT_ZERO_PRICE_ROUTER_CATALOG',
                          promotion_removed=False,
                          returned_model_id=data.get('id'))
            return result
        matches=[]
        for endpoint in data['endpoints']:
            if not isinstance(endpoint, dict):
                continue
            quote = endpoint.get('pricing')
            if not isinstance(quote, dict):
                continue
            try:
                pair = tuple(_decimal(quote.get(k)) for k in ('prompt','completion'))
            except ValueError:
                continue
            if pair != current:
                continue  # Do not substitute Flex/Fast/another provider quote.
            discount = _decimal(quote.get('discount', 0))
            if discount >= 1:
                raise ValueError('REFERENCE_PRICE_DISCOUNT_INVALID')
            # Negative discount means a markup. The user only excludes promotions.
            divisor = Decimal(1) - max(discount, Decimal(0))
            normal = tuple(n/divisor for n in pair)
            if any(not math.isfinite(float(n*1000000)) for n in normal):
                raise ValueError('REFERENCE_PRICE_NUMBER_INVALID')
            matches.append((normal, {'provider':str(endpoint.get('provider_name') or '')[:128],
                'tag':str(endpoint.get('tag') or '')[:256], 'discount':float(discount),
                'availability_status':endpoint.get('status')}))
        values = {pair for pair,_ in matches}
        if not values:
            raise ValueError('REFERENCE_PRICE_MATCH_MISSING')
        # Choose one complete provider quote after removing promotions. A live
        # routing outage does not erase its published reference price.
        # Equal advertised prices may hide different provider promotions.
        normal, selected = min(matches, key=lambda item: (sum(item[0]), item[0],
            item[1]['provider'].casefold(), item[1]['tag']))
        result.update(status='VERIFIED', prompt=float(normal[0]), completion=float(normal[1]),
                      endpoints=[e for pair,e in matches if pair==normal], selected_endpoint=selected,
                      selection='LOWEST_UNDISCOUNTED_PAIR_EQUAL_TOKEN_MIX',
                      promotion_removed=selected['discount']>0)
        if exact_preview_redirect:
            result.update(resolved_model_id=returned_model_id,
                          alias_evidence='EXACT_PREVIEW_SUFFIX_SUCCESSOR_FROM_REQUESTED_ENDPOINT')
    except ValueError as error:
        result['reason'] = str(error)
    return result


def enrich_records(records, fetch, *, prior_records=None, max_checks=None,
                   deadline_seconds=None, max_workers=4):
    """Use the existing public transport/receipt writer supplied by the owner.

    Unchanged endpoint evidence is reused for a bounded period. New, changed,
    and expired entries are checked incrementally under one request/time budget;
    a failed endpoint must never cancel unrelated quotes.
    """
    records = deepcopy(records)
    quotes={r['model_id']:r['operational']['pricing'] for r in records if 'operational' in r}
    canonicals={r['model_id']:r['operational'].get('canonical_slug') for r in records
                if r.get('operational',{}).get('canonical_slug')}
    aliases={r['model_id']:r['operational'].get('alias_target') for r in records
             if r.get('operational',{}).get('alias_target')}
    prior={}
    for row in prior_records or []:
        model_id=row.get('model_id')
        regular=(row.get('operational') or {}).get('regular_pricing')
        if isinstance(model_id,str) and isinstance(regular,dict):
            prior[model_id]=regular
    if max_checks is not None and (type(max_checks) is not int or max_checks < 0):
        raise ValueError('REFERENCE_PRICE_CHECK_BUDGET_INVALID')
    if deadline_seconds is not None and (not isinstance(deadline_seconds,(int,float))
            or isinstance(deadline_seconds,bool) or deadline_seconds <= 0):
        raise ValueError('REFERENCE_PRICE_TIME_BUDGET_INVALID')
    started=time.monotonic()

    def reusable(model_id,pricing):
        old=prior.get(model_id)
        if not old or old.get('policy') not in COMPATIBLE_POLICIES:
            return None
        try:
            if old.get('catalog_quote_sha256') != quote_binding(pricing):
                return None
            stamp=datetime.fromisoformat(str(old.get('retrieved_at','')).replace('Z','+00:00'))
            age=(datetime.now(timezone.utc)-stamp).total_seconds()
        except (TypeError,ValueError):
            return None
        ttl=30*86400 if old.get('status')=='VERIFIED' else 7*86400
        if not 0 <= age <= ttl:
            return None
        return {**deepcopy(old),'cache_reused':True}

    def one(item):
        model_id,pricing=item
        for attempt in range(3):
            if deadline_seconds is not None and time.monotonic()-started >= deadline_seconds:
                return model_id,dict(policy=POLICY,status='UNVERIFIED',
                    reason='REFERENCE_PRICE_REFRESH_BUDGET_EXHAUSTED')
            try:
                payload,evidence=fetch(model_id,endpoint_url(model_id))
                return model_id,{**regular_price(model_id,pricing,payload,
                    canonical_model_id=canonicals.get(model_id)),**evidence}
            except Exception as error:
                reason=str(error) if isinstance(error,ValueError) and re.fullmatch('[A-Z0-9_]+',str(error)) else 'REFERENCE_PRICE_FETCH_FAILED'
                retryable = reason in {'EXTERNAL_METADATA_TIMEOUT', 'EXTERNAL_METADATA_CONNECTION_FAILED',
                    'EXTERNAL_METADATA_TLS_CONNECTION', 'EXTERNAL_METADATA_DNS_FAILED',
                    'EXTERNAL_METADATA_FETCH_FAILED', 'REFERENCE_PRICE_FETCH_FAILED'} or bool(
                    re.fullmatch(r'EXTERNAL_METADATA_HTTP_(408|425|429|5[0-9]{2})', reason))
                if not retryable or attempt == 2:
                    return model_id,dict(policy=POLICY,status='UNVERIFIED',reason=reason)
                time.sleep(0.5 * (2 ** attempt))
    results={}
    pending=[]
    for model_id,pricing in quotes.items():
        if model_id in aliases:
            continue
        cached=reusable(model_id,pricing)
        if cached is not None:
            results[model_id]=cached
            continue
        old=prior.get(model_id)
        try:
            unchanged=bool(old and old.get('catalog_quote_sha256')==quote_binding(pricing))
        except ValueError:
            unchanged=False
        pending.append((1 if unchanged else 0,model_id,pricing))
    pending.sort(key=lambda item:(item[0],item[1]))
    selected=pending if max_checks is None else pending[:max_checks]
    deferred=pending[len(selected):]
    for _,model_id,pricing in deferred:
        value=dict(policy=POLICY,status='UNVERIFIED',model_id=model_id,
                   reason='REFERENCE_PRICE_REFRESH_DEFERRED')
        try:
            value['catalog_quote_sha256']=quote_binding(pricing)
        except ValueError:
            value['reason']='REFERENCE_PRICE_NUMBER_INVALID'
        results[model_id]=value
    if selected:
        with ThreadPoolExecutor(max_workers=max(1,min(int(max_workers),4)),
                                thread_name_prefix='reference-price') as pool:
            results.update(dict(pool.map(one,((mid,price) for _,mid,price in selected))))
    for mid,target in aliases.items():
        resolved=results.get(target)
        if target not in quotes or target in aliases or not resolved:
            results[mid]=dict(policy=POLICY,status='UNVERIFIED',reason='REFERENCE_PRICE_ALIAS_TARGET_MISSING')
            continue
        try:
            same_quote=quote_binding(quotes[mid]) == quote_binding(quotes[target])
        except ValueError:
            same_quote=False
        results[mid]={**deepcopy(resolved),'model_id':mid,'resolved_model_id':target,
                      'alias_evidence':'OFFICIAL_CATALOG_ALIAS_TARGET',
                      'alias_catalog_quote_matches_target':same_quote,
                      'resolved_catalog_quote_sha256':quote_binding(quotes[target]),
                      'catalog_quote_sha256':quote_binding(quotes[mid])}
    for row in records:
        if 'operational' in row:
            row['operational']['regular_pricing']=results[row['model_id']]
    return records
