"""Verification belongs to a target, not to its editable row or display label."""
from copy import deepcopy
from .contracts import canonical_sha256


def network_identity(settings):
    p = settings['preferences']
    return {k:p.get(k) for k in ('proxy_mode','proxy_address')}


def api_identity(settings, service):
    credential = next((r for r in settings['credential_references']
        if r['credential_ref'] == service['credential_ref']), None)
    return canonical_sha256({'service':{k:v for k,v in service.items()
        if k not in {'connection_status','display_name'}},
        'credential_metadata':credential, 'network':network_identity(settings)})


def cli_identity(settings, service, model):
    return canonical_sha256({'service':{k:v for k,v in service.items()
        if k not in {'models','connection_status','display_name'}},
        'model':{k:v for k,v in model.items() if k not in {'connection_status','display_name'}},
        'network':network_identity(settings)})


def invalidate_changed_targets(previous, proposed):
    result = deepcopy(proposed)
    old_api = {r['config_id']:r for r in previous['model_services']}
    for row in result['model_services']:
        old = old_api.get(row['config_id'])
        same = old is not None and api_identity(previous,old) == api_identity(result,row)
        row['connection_status'] = old['connection_status'] if same and row['connection_status'] != 'UNVERIFIED' else 'UNVERIFIED'
    old_cli = {r['config_id']:r for r in previous['cli_services']}
    for service in result['cli_services']:
        old = old_cli.get(service['config_id'])
        models = {r['profile_ref']:r for r in old['models']} if old else {}
        for row in service['models']:
            before = models.get(row['profile_ref'])
            same = before is not None and cli_identity(previous,old,before) == cli_identity(result,service,row)
            row['connection_status'] = before['connection_status'] if same and row['connection_status'] != 'UNVERIFIED' else 'UNVERIFIED'
        states = {r['connection_status'] for r in service['models']}
        service['connection_status'] = 'AVAILABLE' if 'AVAILABLE' in states else 'INVALID' if 'INVALID' in states else 'UNVERIFIED'
    return result
