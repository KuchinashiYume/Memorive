"""Exact provider-declared compatibility IDs, scoped to the official endpoint."""
from urllib.parse import urlsplit
from collections.abc import Mapping
SOURCE='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
VERIFIED_AT='2026-09-14'
FLASH_ALIASES={'deepseek-v4-flash':'deepseek-flash','deepseek-v4-flash-vision-exp':'deepseek-flash'}
def official_deepseek(service):
    if 'deepseek' not in str(service.get('provider','')).lower():return False
    raw=service.get('api_base_url') or 'https://api.deepseek.com'
    try:
        url=urlsplit(raw)
        return url.scheme=='https' and url.hostname=='api.deepseek.com' and url.username is None and url.password is None
    except (TypeError,ValueError):return False
def catalog_alias(service,requested,catalog):
    alias=FLASH_ALIASES.get(requested)
    if official_deepseek(service) and alias in catalog:
        return {'canonical_model':alias,'requested_compatibility_model':requested,'source':SOURCE,'verified_at':VERIFIED_AT}
    return None

def matches_result_model(result, requested):
    if not isinstance(result, Mapping):return False
    returned=result.get('returned_model')
    if returned == requested:return isinstance(requested,str) and bool(requested)
    receipt=result.get('execution_receipt',result)
    from .cli_templates import receipt_model_bound
    if receipt_model_bound(receipt,requested):return True
    if not isinstance(receipt,Mapping) or receipt.get('profile_kind')!='API':return False
    alias=receipt.get('provider_model_alias')
    return isinstance(alias,Mapping) and bool(FLASH_ALIASES.get(requested)) and alias.get('requested_compatibility_model')==requested and alias.get('canonical_model')==returned==FLASH_ALIASES[requested] and alias.get('source')==SOURCE and alias.get('endpoint_origin')=='https://api.deepseek.com'
