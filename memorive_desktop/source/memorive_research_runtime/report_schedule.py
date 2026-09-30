"""SHARED report due-times; preserve the established RESEARCH_REPORTS calendar/timezone."""
from calendar import monthrange
from datetime import datetime, timedelta
import re
from research_reports.policy import ZONE
from .common import read,write,sealed

def defaults():
    return {'daily_time':'00:00','weekly_time':'00:00','weekly_day':0,
            'monthly_time':'00:00','monthly_day':1}

def validate(config):
    if not isinstance(config,dict) or set(config)!=set(defaults()):
        raise ValueError('REPORT_SCHEDULE_FIELDS_INVALID')
    for key in ('daily_time','weekly_time','monthly_time'):
        if not isinstance(config[key],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',config[key]):
            raise ValueError('REPORT_SCHEDULE_TIME_INVALID')
    for key,lo,hi in (('weekly_day',0,6),('monthly_day',1,31)):
        if type(config[key]) is not int or not lo<=config[key]<=hi:
            raise ValueError('REPORT_SCHEDULE_DAY_INVALID')
    return dict(config)

def get(root):
    path=root/'report_schedule.json'
    if not path.exists():
        return {'schema_version':'DesktopReportSchedule-v1','revision':0,'timezone':'Asia/Shanghai','config':defaults()}
    value=read(path)
    if value.get('schema_version')!='DesktopReportSchedule-v1' or type(value.get('revision')) is not int or value['revision']<1:
        raise ValueError('REPORT_SCHEDULE_INVALID')
    validate(value['config'])
    return value

def save(runtime,config,expected_revision):
    with runtime.lock:
        if runtime.closed:raise ValueError('RESEARCH_CLOSED')
        current=get(runtime.root)
        if type(expected_revision) is not int or expected_revision!=current['revision']:
            raise ValueError('REPORT_SCHEDULE_REVISION_CONFLICT')
        value=sealed({'schema_version':'DesktopReportSchedule-v1','revision':current['revision']+1,
                      'timezone':'Asia/Shanghai','config':validate(config)})
        write(runtime.root/'report_schedule.json',value)
        return value

def due_at(window,config):
    config=validate(config)
    end=datetime.fromisoformat(window['end']).astimezone(ZONE)
    period=window['period']
    if period=='weekly':
        end+=timedelta(days=(config['weekly_day']-end.weekday())%7)
    elif period=='monthly':
        end=end.replace(day=min(config['monthly_day'],monthrange(end.year,end.month)[1]))
    elif period!='daily':raise ValueError('RESEARCH_PERIOD_INVALID')
    hour,minute=map(int,config[period+'_time'].split(':'))
    return end.replace(hour=hour,minute=minute,second=0,microsecond=0)
