"""SHARED typed report identity; legacy enrichment is read-only."""
from datetime import datetime,timedelta
from research_reports.policy import ZONE,validate_window
from .common import read

LABELS={'daily':'日报','weekly':'周报','monthly':'月报'}

def previous_window(period,current):
    end=current.astimezone(ZONE).replace(hour=0,minute=0,second=0,microsecond=0)
    if period=='daily':start=end-timedelta(days=1)
    elif period=='weekly':
        end-=timedelta(days=end.weekday());start=end-timedelta(days=7)
    elif period=='monthly':
        end=end.replace(day=1);start=(end-timedelta(days=1)).replace(day=1)
    else:raise ValueError('RESEARCH_PERIOD_INVALID')
    return {'period':period,'start':start.isoformat(),'end':end.isoformat()}

def window_key(window):
    return (window['period'],datetime.fromisoformat(window['start']).astimezone(ZONE).isoformat(),
            datetime.fromisoformat(window['end']).astimezone(ZONE).isoformat())

def title(window,revision=1):
    start=datetime.fromisoformat(window['start']).astimezone(ZONE).date().isoformat()
    end=(datetime.fromisoformat(window['end']).astimezone(ZONE)-timedelta(days=1)).date().isoformat()
    return LABELS[window['period']]+' · '+start+(' — '+end if window['period']!='daily' else '')+(' · v'+str(revision) if revision>1 else '')

def project(root,items=None):
    if items is None:items=[read(p) for p in (root/'runs').glob('*/state.json')]
    result=[dict(item) for item in items];groups={}
    for item in result:
        if item.get('kind')!='report':continue
        path=root/'runs'/item['run_id'];window=item.get('report_window')
        try:body=read(path/'result.json') if (path/'result.json').exists() else None
        except (OSError,ValueError):body=None
        if body and body.get('window_start') and body.get('window_end'):
            window={'period':body['period'],'start':body['window_start'],'end':body['window_end']}
        if not window:continue
        validate_window(window['period'],window['start'],window['end'],window['end'])
        item['report_window']=window
        groups.setdefault(window_key(window),[]).append((item,body is not None))
    for rows in groups.values():
        number=0
        for item,produced in sorted(rows,key=lambda pair:(pair[0]['created_at'],pair[0]['run_id'])):
            explicit=item.get('report_revision')
            revision=explicit if type(explicit) is int and explicit>0 else number+1
            if produced:number=max(number,revision)
            item['report_revision']=revision;item['title']=title(item['report_window'],revision)
    return result

def next_revision(root,window):
    versions=[item['report_revision'] for item in project(root) if item.get('report_window')
              and window_key(item['report_window'])==window_key(window)
              and (root/'runs'/item['run_id']/'result.json').exists()]
    return max(versions,default=0)+1
