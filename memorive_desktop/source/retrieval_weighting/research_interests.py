"""Explicit-query interest signals; no mining of imported conversations."""
import hashlib,re,math
from datetime import datetime,timezone
from memorive_research_workspace.evidence import tokens

def term_hashes(text):
    stop={'the','and','for','with','what','how','are','this','these','from','that','have','has','哪些','什么','研究','文献','资料','帮我'}
    return sorted({hashlib.sha256(t.encode('utf-8')).hexdigest() for t in tokens(text) if t not in stop})[:64]

def signal(snapshot,text,*,channel):
    # Exploratory channels must remain independent of recent behaviour.
    if channel not in {'CORE','ADJACENT'} or not snapshot.get('enabled'):return 0.0
    current=set(term_hashes(text));score=0.0
    for event in snapshot.get('events',[]):
        known=set(event['term_hashes']);overlap=len(current&known)/max(1,len(known))
        score=max(score,overlap*event['decay'])
    return round(min(1.0,score),6)

def snapshot(store,*,enabled,as_of=None,project=None,window_days=30,half_life=7):
    now=as_of or datetime.now(timezone.utc);events=[]
    if enabled:
        with store.tx() as db:
            for row in db.execute("SELECT seq,object_id,at,body FROM events WHERE event='EXPLICIT_RESEARCH_INTEREST' ORDER BY seq DESC LIMIT 300"):
                import json
                data=json.loads(row['body'])
                if project is not None and data.get('project')!=project:continue
                if store.get('interest_exclusion',str(row['seq']),db=db):continue
                if not store.get('thread',row['object_id'],db=db):continue
                age=(now-datetime.fromisoformat(row['at'])).total_seconds()/86400
                if not 0<=age<=window_days or not data.get('term_hashes'):continue
                events.append({'event_id':row['seq'],'thread_id':row['object_id'],'query_hash':data['query_hash'],'project':data['project'],
                    'term_hashes':data['term_hashes'],'decay':round(math.pow(.5,age/half_life),8)})
    from memorive_research_workspace.store import digest
    result={'schema_version':'DesktopExplicitResearchInterest-v1','enabled':bool(enabled),'events':events,
        'window_days':window_days,'half_life_days':half_life,'raw_history_mined':False,'long_term_profile_mutated':False}
    result['snapshot_hash']=digest(result)
    return result
