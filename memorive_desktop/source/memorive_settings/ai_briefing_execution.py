"""AI briefing is a consumer of the shared qualified execution and call ledger."""
from .report_execution import ReportProfileExecution
import json,re

class AIBriefingExecution(ReportProfileExecution):
    periods=('briefing',)
    purpose_prefix='ai_'
    directory_name='ai_briefing_profiles'

    def cancel(self,snapshot_id):
        if not isinstance(snapshot_id,str) or not re.fullmatch('[a-fA-F0-9]{64}',snapshot_id) or not (self.root/(snapshot_id+'.json')).is_file():
            raise ValueError('AI_SNAPSHOT_INVALID')
        self.controller.store._atomic_write(self.root/(snapshot_id+'.cancel.json'),{'cancel_requested':True})
        return {'status':'CANCELLING'}

    def execute(self,**kwargs):
        from .call_ledger import execution_control,ExecutionControlSignal
        snapshot_id=kwargs.get('snapshot_id')
        if not isinstance(snapshot_id,str) or not re.fullmatch('[a-fA-F0-9]{64}',snapshot_id):raise ValueError('AI_SNAPSHOT_INVALID')
        def check():
            if (self.root/(snapshot_id+'.cancel.json')).exists():raise ExecutionControlSignal('CANCELLED')
        try:
            with execution_control(check):
                check();return super().execute(**kwargs)
        except ExecutionControlSignal as signal:
            result={'status':'CANCELLED','reason':'AI_MODEL_CANCELLED','execution_receipt':signal.execution_receipt or {}}
            self.controller.store._atomic_write(self.root/(snapshot_id+'.cancelled-result.json'),result)
            return result
