"""Read-only recovery DTOs over existing job and call receipts."""
from copy import deepcopy
import json,re,os,stat
from pathlib import Path
from typing import Any, Mapping

TERMINAL={'SUCCEEDED','FAILED','CANCELLED'}

def retry_counters(root: Path | None, job_id: str, cache: dict) -> dict[str, dict[str, Any]]:
    if root is None or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,191}',job_id):return {}
    folder=(root/job_id/'model_calls').resolve()
    if not folder.is_relative_to(root.resolve()) or not folder.is_dir():return {}
    records=[]
    # Windows directory enumeration already returns the file metadata. Avoid
    # three additional stat syscalls for every cached receipt on every refresh.
    with os.scandir(folder) as scan:
        entries = [entry for entry in scan if entry.name.startswith('call-') and entry.name.endswith('.post.json')]
    for entry in entries:
        path = Path(entry.path)
        try:
            info = entry.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):continue
            stamp=(info.st_mtime_ns,info.st_size,info.st_ctime_ns)
            cached=cache.get(str(path))
            if cached and cached[0]==stamp:value=cached[1]
            else:
                value=json.loads(path.read_text(encoding='utf8'))
                if not isinstance(value, dict):continue
                er=value.get('execution_receipt') or {}
                if not isinstance(er, dict):continue
                node=er.get('node_id')
                if not node:
                    pre=path.with_name(path.name.replace('.post.json','.pre.json'))
                    node=json.loads(pre.read_text(encoding='utf8')).get('node_id')
                value={**value,'_node_id':node};cache[str(path)]=(stamp,value)
            retry=value.get('retry') or {};limit=retry.get('configured_retries');used=retry.get('consumed_retries')
            if not all(isinstance(x,int) and not isinstance(x,bool) and x>=0 for x in (limit,used)) or used>limit:continue
            if not value.get('_node_id'):continue
            records.append(value)
        except (OSError,ValueError,TypeError,AttributeError):continue
    result={}
    for value in sorted(records,key=lambda x:x.get('sequence',0)):
        retry=value['retry'];node=value['_node_id'];old=result.get(node,{})
        result[node]={'retry_consumed':retry['consumed_retries'],
            'retry_remaining':retry['configured_retries']-retry['consumed_retries'],
            'retry_runtime_configured':retry['configured_retries'],
            'retry_sequence':value.get('sequence'),'retry_status':value.get('status'),
            'retry_reason':value.get('reason'),'retry_evidence_source':'LATEST_MODEL_CALL_RECEIPT',
            'retry_scope':retry.get('scope'), 'retry_page_number':retry.get('page_number'),
            'retry_model_role':retry.get('model_role'),
            'node_total_retries':old.get('node_total_retries',0)+(1 if retry['consumed_retries']>0 else 0)}
    return result

def lineage_partition(current: list[dict], history: list[dict]) -> tuple[list[dict],list[dict]]:
    """Only published successor links supersede a predecessor, never equal titles."""
    all_rows=[*current,*history];by_id={r['job_id']:r for r in all_rows}
    # Connected components include historical branches made by returning to an
    # older execution. A published successor is evidence of the same task;
    # equal display names alone never establish that relationship.
    groups={key:key for key in by_id}
    def root(key):
        while groups[key] != key:
            groups[key]=groups[groups[key]]
            key=groups[key]
        return key
    for row in all_rows:
        predecessor=row.get('predecessor_job_id')
        if predecessor and predecessor in by_id:
            groups[root(row['job_id'])]=root(predecessor)
    children={row.get('predecessor_job_id') for row in all_rows if row.get('predecessor_job_id') in by_id}
    heads={}
    for row in all_rows:
        if row['job_id'] in children:continue
        key=root(row['job_id']);prior=heads.get(key)
        if prior is None or (row.get('created_at') or '',row['job_id'])>(prior.get('created_at') or '',prior['job_id']):
            heads[key]=row
    live=[];past=[]
    for original in all_rows:
        row=deepcopy(original);head=heads.get(root(row['job_id']))
        if head and head['job_id']!=row['job_id']:
            row['superseded_by_job_id']=head['job_id']
        if row.get('superseded_by_job_id'):
            row['collection_state']='HISTORY';row['history_reason']='SUPERSEDED_EXECUTION'
        (past if row.get('collection_state')=='HISTORY' else live).append(row)
    return live,past
