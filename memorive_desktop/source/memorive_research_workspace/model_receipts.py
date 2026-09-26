"""Keep a failed model attempt's existing receipt attached to the local job."""
from memorive_settings.provider_catalog_aliases import matches_result_model

class ModelExecutionError(ValueError):
    def __init__(self, code, receipt):
        super().__init__(code)
        self.execution_receipt = receipt or {}


def verified_result(result, snapshot):
    receipt = result.get('execution_receipt') or {}
    if result.get('status') != 'PASS' or receipt.get('status') != 'PASS':
        raise ModelExecutionError('CHAT_MODEL_EXECUTION_FAILED', receipt)
    if result.get('requested_model') != snapshot['model_name'] or not matches_result_model(result,snapshot['model_name']):
        raise ModelExecutionError('CHAT_MODEL_IDENTITY_MISMATCH', receipt)
    if not receipt.get('behavior_sha256') or not receipt.get('token_usage'):
        raise ModelExecutionError('CHAT_MODEL_RECEIPT_INCOMPLETE', receipt)
    return dict(result, model_name=snapshot['model_name'])
