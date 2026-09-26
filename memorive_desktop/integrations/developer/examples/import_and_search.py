"""Runnable local automation example. It imports only the explicit file."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from memorive import MemoClient
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--command',required=True,help='JSON argument list, e.g. ["C:/Memo/Memorive.exe","--memo-agent"]')
p.add_argument('--workspace',required=True);p.add_argument('--client',required=True)
p.add_argument('--connection',required=True);p.add_argument('--project',required=True);p.add_argument('--file',required=True);p.add_argument('--query',default='method')
a=p.parse_args()
c=MemoClient(json.loads(a.command),a.workspace,a.client);c.capabilities()
preview=c.call('memo.import_preview',connection_id=a.connection,paths=[str(Path(a.file).resolve())])
job=c.call('memo.import_commit',connection_id=a.connection,preview_id=preview['id'],preview_hash=preview['preview_hash'])
if job['status']!='COMPLETE':raise RuntimeError('IMPORT_NOT_COMPLETE')
result=c.call('memo.search',project=a.project,query=a.query)
print(json.dumps({'import_status':job['status'],'citations':result['results'],'events':c.events()},ensure_ascii=False,indent=2))
