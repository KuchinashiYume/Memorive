"""General capability defaults; contains no personal state or exam results."""
import json

def capabilities():
    value = json.loads('{"schema_version":"CapabilityStateProjection-v1","revision":"desktop-desktop-local-v1","capabilities":[{"capability_id":"model.analysis_primary","available":true,"enabled":false,"eligible":false,"blocked":true,"reason":"ROUTE_DISABLED_BY_CURRENT_AUTHORITY"},{"capability_id":"model.card_distillation","available":true,"enabled":false,"eligible":false,"blocked":true,"reason":"ROUTE_DISABLED_BY_CURRENT_AUTHORITY"},{"capability_id":"provider.external_connectivity","available":false,"enabled":false,"eligible":false,"blocked":true,"reason":"Configuration_EXTERNAL_PROVIDER_CALLS_FORBIDDEN"},{"capability_id":"credential.reference_store","available":true,"enabled":true,"eligible":true,"blocked":false,"reason":"WINDOWS_CREDENTIAL_MANAGER_REFERENCE_ONLY"},{"capability_id":"settings.atomic_persistence","available":true,"enabled":true,"eligible":true,"blocked":false,"reason":"LOCAL_PROFILE_ATOMIC_STORE"}]}')

    value['revision']='desktop-evo-e2-local-v1'
    value['capabilities'].extend([
        dict(capability_id='review.local_numeric',available=True,enabled=True,eligible=True,blocked=False,reason='LOCAL_DETERMINISTIC_BOUND_RULES_NO_MODEL'),
        dict(capability_id='review.cloud_logic',available=True,enabled=False,eligible=False,blocked=True,reason='REQUIRES_CLOUD_PROFILE_AND_PER_DOCUMENT_SELECTION'),
        dict(capability_id='review.reference_exam',available=True,enabled=False,eligible=False,blocked=True,reason='REQUIRES_CLOUD_PROFILE_AND_EXAM_AUTHORIZATION_REFERENCE_ONLY'),
    ])
    return value
