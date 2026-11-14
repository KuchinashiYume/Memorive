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
    from .cli_templates import is_custom,stable_configuration_identity
    if is_custom(service):
        return canonical_sha256({'configuration':stable_configuration_identity(service,model),'network':network_identity(settings)})
    return canonical_sha256({'service':{k:v for k,v in service.items()
        if k not in {'models','connection_status','display_name'}},
        'model':{k:v for k,v in model.items() if k not in {'connection_status','display_name'}},
        'network':network_identity(settings)})


def adopt_saved_verifications(store):
    """Rewrite verifications that an earlier version saved, and return how many.

    Earlier versions bound a verification to the sha256 of the tool's files. While those
    files are still the ones verified, the value is replaced by the current form, which
    does not depend on them; the tool can then update itself without losing it.
    """
    from .cli_templates import is_custom,stable_configuration_identity,verification_current
    envelope = store.load()
    settings = deepcopy(envelope['settings'])
    rewritten = 0
    for service in settings['cli_services']:
        if not is_custom(service):
            continue
        for model in service['models']:
            binding = model.get('verification')
            current = stable_configuration_identity(service,model)
            if binding and binding.get('configuration_sha256') != current and verification_current(binding,service,model):
                binding['configuration_sha256'] = current
                rewritten += 1
    if rewritten:
        store.save(settings, expected_revision=envelope['revision'])
    return rewritten


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
            from .cli_templates import is_custom
            if is_custom(service):
                from .cli_templates import stable_configuration_identity,verification_current
                valid=same and verification_current(before.get('verification'),service,row)
                # A value saved by an earlier version is rewritten in the current form.
                row['verification']=dict(deepcopy(before['verification']),configuration_sha256=stable_configuration_identity(service,row)) if valid else None
                row['connection_status']=before['connection_status'] if valid else 'UNVERIFIED'  # Server binding wins over a stale UI draft.
        states = {r['connection_status'] for r in service['models']}
        service['connection_status'] = 'AVAILABLE' if 'AVAILABLE' in states else 'INVALID' if 'INVALID' in states else 'UNVERIFIED'
    return result
