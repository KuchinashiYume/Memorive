"""Public LiveBench release tables; data only, never execute downloaded JS.

Source contract: LiveBench/new-livebench README, src/lib/constants.js and
src/Table/Averaging.js. Seven category means have equal weight. Raw model
identity, effort, release and input hashes remain visible; no inferred grades.
"""
import csv
from datetime import date
import io
import re
from urllib.parse import urlsplit

SUFFIX='/src/lib/constants.js'
DEFAULT_ENDPOINT='https://raw.githubusercontent.com/LiveBench/new-livebench/main'+SUFFIX
CATEGORY_METRICS={'Reasoning':'livebench_reasoning','Coding':'livebench_coding',
    'Agentic Coding':'livebench_agentic_coding','Mathematics':'livebench_math',
    'Data Analysis':'livebench_data_analysis','Language':'livebench_language','IF':'livebench_instruction_following'}
def release_urls(endpoint,body):
    url=urlsplit(endpoint)
    if not url.path.endswith(SUFFIX) or url.query or url.fragment:
        raise ValueError('LIVEBENCH_RELEASE_ENDPOINT_INVALID')
    text=body.decode('utf-8-sig')
    match=re.search(r'export\s+const\s+RELEASES\s*=\s*\[([^\]]+)\]',text)
    if not match: raise ValueError('LIVEBENCH_RELEASE_LIST_MISSING')
    releases=re.findall(r'["\'](\d{4}-\d{2}-\d{2})["\']',match[1])
    if not releases: raise ValueError('LIVEBENCH_RELEASE_LIST_EMPTY')
    for value in releases: date.fromisoformat(value)
    release=max(releases); stamp=release.replace('-','_'); base=endpoint[:-len(SUFFIX)]+'/public/'
    return release,[endpoint,base+f'categories_{stamp}.json',base+f'table_{stamp}.csv']

def normalized_rows(bundle):
    categories=bundle['categories']
    if not isinstance(categories,dict) or set(categories)!=set(CATEGORY_METRICS):
        raise ValueError('LIVEBENCH_CATEGORIES_CHANGED')
    columns=[]
    for tasks in categories.values():
        if not isinstance(tasks,list) or not tasks or not all(isinstance(t,str) and t for t in tasks):
            raise ValueError('LIVEBENCH_TASK_COLUMNS_INVALID')
        columns.extend(tasks)
    if len(columns)!=len(set(columns)): raise ValueError('LIVEBENCH_TASK_COLUMN_DUPLICATE')
    table=csv.DictReader(io.StringIO(bundle['table_csv']))
    if not table.fieldnames or set(table.fieldnames)!={'model',*columns}:
        raise ValueError('LIVEBENCH_TABLE_SCHEMA_CHANGED')
    seen=set(); results=[]
    for row in table:
        model=row['model']
        if not model or model in seen: raise ValueError('LIVEBENCH_MODEL_DUPLICATE')
        seen.add(model); values={}
        for col in columns:
            raw=row[col]
            number=None if raw in ('','-','NA','N/A') else float(raw)
            if number is not None and not 0<=number<=100: raise ValueError('LIVEBENCH_SCORE_INVALID')
            values[col]=number
        metrics={}
        for category,tasks in categories.items():
            present=[values[col] for col in tasks if values[col] is not None]
            # Never make missing category coverage look like a complete global.
            metrics[CATEGORY_METRICS[category]]=sum(present)/len(present) if len(present)==len(tasks) else None
        complete=all(value is not None for value in metrics.values())
        metrics['livebench_global']=sum(metrics.values())/len(metrics) if complete else None
        base,effort=model,None
        match=re.search(r'-(none|low|medium|high|xhigh|max|ultra)(?:-effort)?$',model)
        if match: base,effort=model[:match.start()],match[1]
        results.append(dict(model=model,base=base,effort=effort,metrics=metrics,complete=complete))
    if len(results)>20000: raise ValueError('EXTERNAL_RESPONSE_ITEM_ENVELOPE_EXCEEDED')
    return results
