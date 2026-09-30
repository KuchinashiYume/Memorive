"""Research task projection using the existing Desktop task cards and inspectors."""
from datetime import datetime

STEPS=(('WAITING','冻结研究设置'),('COLLECTING','检索文献来源'),('RESOLVING_IDENTITIES','核验身份与本地关系'),
       ('COMPOSING','应用反馈与编排推荐'),('PUBLISHING','保存 Markdown 与投递'),('COMPLETE','结果可用'))
REPORT_STEPS=(('WAITING','确认报告周期'),('COLLECTING','汇总已完成成果'),('COMPOSING','生成报告'),
              ('PUBLISHING','保存至资料库'),('NOTIFYING','发送报告提醒'),('COMPLETE','结果可用'))

AI_STEPS=(('WAITING','确认日期与模型'),('COLLECTING','读取公开来源'),('COMPOSING','整理 AI 近况'),('VALIDATING','核对引用来源'),('PUBLISHING','保存至资料库'),('NOTIFYING','发送提醒'),('COMPLETE','结果可用'))

def steps_for(item):
    if item.get('kind')=='ai_briefing':return AI_STEPS
    return REPORT_STEPS if item.get('kind')=='report' else STEPS

def bindings(runtime):
    from memorive_workflow.node_progress import project,is_live
    rows=[]
    for item in runtime.task_list()['rows']:
        steps=steps_for(item)
        stage=item.get('failed_stage') if item['status'] in {'FAILED','CANCELLED','INTERRUPTED'} else item.get('stage')
        index=next((i for i,(key,_) in enumerate(steps) if key==stage),0)
        terminal=item['status'] not in {'QUEUED','RUNNING'}
        success=item['status'] in {'SUCCEEDED','PARTIAL'}
        nodes=[]
        for i,(key,name) in enumerate(steps):
            state='COMPLETED' if success or i<index else ('FAILED' if item['status']=='FAILED' else 'CANCELLED') if terminal and i==index else 'RUNNING' if i==index and item['status']=='RUNNING' else 'NOT_STARTED'
            if key=='NOTIFYING' and item.get('notification_error'):state='FAILED'
            model='本地生成 · 不调用模型' if item['kind']=='report' else '元数据与本地规则 · 不调用模型'
            if item['kind'] in {'report','ai_briefing'} and key=='COMPOSING' and item.get('report_model'):
                frozen=item['report_model']
                model=frozen.get('display_name') or frozen['model_name']
            nodes.append({'node_id':key,'number':str(i+1).zfill(2),'name':name,'purpose':name,'model':model,
                'state':state,'model_change_eligible':False,'retry_change_eligible':False,'enable_toggle_eligible':False,
                'rollback_eligible':False,'optional':False,'configuration_source':'RESEARCH_FROZEN_SNAPSHOT',
                'node_progress':project(item.get('node_progress',{}),key,job_id=item['run_id'],attempt_id=item['run_id'],
                    active=state=='RUNNING' and not item.get('progress_cancelled'),
                    live=is_live(item,key))})
        rows.append({'run':item['run_id'],'job_id':item['run_id'],'attempt_id':item['run_id'],
            'display_name':('外部文献·' if item['kind']=='discovery' else '')+item['title'],
            'control_state':'SUCCEEDED' if success else item['status'],'workflow_kind':item['workflow_kind'],
            'created_at':item['created_at'],'updated_at':item.get('finished_at') or item['created_at'],'completed_at':item.get('finished_at'),
            'collection_state':'HISTORY' if item['history'] else 'CURRENT','historical_read_only':item['history'],
            'workflow_nodes':nodes,'progress':{'completed_units':sum(n['state']=='COMPLETED' for n in nodes),'total_units':len(nodes)},
            'artifact_count':int(bool(item.get('result_sha256'))),'result_locator':item.get('library_locator'),
            'period':item.get('period'),'report_window':item.get('report_window'),'report_revision':item.get('report_revision'),
            'delivery_repair_available':bool((runtime._run_path(item['run_id'])/'result.json').exists() and (item['status']=='FAILED' or item.get('notification_error'))),
            'source_status':item['status'],'error_code':item.get('error_code'),'synthetic_only':False})
    return rows

def view(runtime,run_id):
    row=next((r for r in bindings(runtime) if r['run']==run_id),None)
    if row is None:raise ValueError('RESEARCH_TASK_NOT_FOUND')
    return {'status':'PASS','projection':{'workflow_kind':row['workflow_kind'],'display_name':row['display_name'],
        'attempt_id':run_id,'state_axes':{'job_control_state':row['control_state']},
        'graph':{'workflow_nodes':row['workflow_nodes']},'progress':row['progress']}}

def log_rows(runtime):
    rows=[]
    for item in runtime.task_list()['rows']:
        labels=dict(steps_for(item))
        stamp=datetime.fromisoformat((item.get('finished_at') or item['created_at']).replace('Z','+00:00'))
        local=stamp.astimezone();run=item['run_id']
        status={'SUCCEEDED':'已完成','PARTIAL':'部分完成','FAILED':'失败','CANCELLED':'已取消','INTERRUPTED':'已中断','RUNNING':'运行中','QUEUED':'等待中'}[item['status']]
        rows.append({'log_entry_id':'activity-'+run,'job_id':run,'title':('外部文献·' if item['kind']=='discovery' else '')+item['title'],
            'summary':status+' · '+labels.get(item.get('stage'),'已停止'),'type_label':{'discovery':'外部文献','report':'周期报告','ai_briefing':'AI 近况'}[item['kind']],
            'event_time_utc':stamp.isoformat(),'event_sequence':1,'date':local.date().isoformat(),
            'age_bucket':runtime.api._work_log._age_bucket(stamp),'ledger_type':'branch','parse_status':'ready',
            'source_locator':'memorive://ledger/branch/'+run,'job_locator':'memorive://job/'+run,
            'state_axis':{'QUEUED':'等待执行','RUNNING':'正在执行','SUCCEEDED':'已完成','PARTIAL':'部分完成',
                          'FAILED':'执行异常','CANCELLED':'已取消','INTERRUPTED':'已中断'}.get(item['status'],'状态未知'),
            'severity':'ERROR' if item['status']=='FAILED' else 'INFO',
            'evidence_locators':[item['library_locator']] if item.get('library_locator') else [],
            'milestones':[],'public_payload':{'workflow_kind':item['workflow_kind'],'control_state':item['status'],
                'external_model_calls':item.get('model_calls',0),'raw_private_content_included':False},
            'related_artifact_id':item.get('library_artifact_id'),'related_artifact_locator':item.get('library_locator')})
    return rows
