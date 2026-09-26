"""KNOWLEDGE_ADMISSION live state transitions for the desktop research-feedback successor.

The historical Research dry-run contracts remain immutable. This successor's owner
uses one SQLite transaction with KNOWLEDGE_FEEDBACK and an idempotent ARTIFACT_REGISTRY registration step.
"""
def transition_feedback(current,target,*,human_confirmed,source_fresh,registry_registered):
    allowed={'review_pending':{'active','rejected'},'active':{'retracted'},'rejected':set(),'retracted':set()}
    if target not in allowed.get(current,set()):raise ValueError('FEEDBACK_TRANSITION_INVALID')
    if not human_confirmed:raise ValueError('HUMAN_REVIEW_REQUIRED')
    if target=='active' and not(source_fresh and registry_registered):raise ValueError('FEEDBACK_ADMISSION_INCOMPLETE')
    return target
