"""Local M14 observations of source freshness, review age and retrieval composition."""
import json
from datetime import datetime,timezone
from .store import now

def health(workspace,project):
    config=workspace.weights.settings(project)['config'];clock=datetime.now(timezone.utc);items=[]
    with workspace.store.tx() as db:
        for proposal in workspace.store.list('feedback',project,db=db):
            issue=[]
            for parent in proposal['parents']:
                try:workspace.index.read(parent['id'],project,expected_hash=parent['content_hash'],db=db)
                except (ValueError,OSError):issue.append('source_changed');break
            reviewed=proposal.get('reviewed_at') or proposal['created_at']
            if proposal['state']=='active' and (clock-datetime.fromisoformat(reviewed)).total_seconds()>config['review_days']*86400:issue.append('review_due')
            items.append({'id':proposal['id'],'state':proposal['state'],'issues':issue,'reviewed_at':proposal.get('reviewed_at'),'revision':proposal['revision'],'title':proposal['title']})
        rows=[json.loads(r['body']) for r in db.execute("SELECT body FROM events WHERE event='M14_RESEARCH_RETRIEVAL' AND object_id=? ORDER BY seq DESC LIMIT 100",(project,))]
    total=sum(r['top_count'] for r in rows);derived=sum(r['derived_count'] for r in rows)
    return {'items':items,'checked_at':now(),'review_days':config['review_days'],'retrievals_observed':len(rows),
            'derived_share':derived/total if total else None,'parent_displacement_frequency':sum(bool(r['displaced_parent_ids']) for r in rows)/len(rows) if rows else None,'displaced_parent_count':sum(len(r['displaced_parent_ids']) for r in rows),
            'quality_verdict':'NOT_ASSESSED','measurement':'observed_top_k_not_scientific_quality'}

def revise(workspace,identity,expected_revision,changes,evidence_ids=None):
    allowed={'title','claim','scope','limitations','derived_type'}
    if not isinstance(changes,dict) or set(changes)-allowed:raise ValueError('FEEDBACK_FIELDS_INVALID')
    with workspace.store.tx() as db:
        old=workspace.store.get('feedback',identity,db=db)
        if not old:raise ValueError('FEEDBACK_NOT_FOUND')
        if old['revision']!=expected_revision:raise ValueError('REVISION_CONFLICT')
        value={k:changes.get(k,old.get(k,'synthesis')) for k in allowed}
        parents=evidence_ids or [r['id'] for r in old['parents']]
        # A revision is a new pending proposal. Accepted artifacts and their citations stay immutable.
        return workspace.feedback.propose(project=old['project'],**value,evidence_ids=parents,origin='desktop_revision',predecessor=identity,db=db)
