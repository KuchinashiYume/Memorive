"""Explicit legacy adapter fixture, not the product default."""
from memorive_settings.external_sources import default_sources
from memorive_settings.contracts import canonical_sha256

def aa_source():
    row=default_sources()[0]
    row.update(display_name='Artificial Analysis',format='ARTIFICIAL_ANALYSIS',
        endpoint='https://artificialanalysis.ai/api/v2/language/models/free',
        credential_env='ARTIFICIAL_ANALYSIS_API_KEY',credential_origin='https://artificialanalysis.ai')
    return row

def configure_aa(service):
    state=service._load()
    state['sources'][0]=aa_source()
    state['sha256']=canonical_sha256(state)
    service.writer._atomic_write(service.path,state)
    return service
