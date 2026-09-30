"""Explicit AI briefing jobs in the existing research task/result/message lifecycle."""
from datetime import datetime, timedelta, timezone, date
import json, re, sqlite3, threading
from .common import read, write, sealed, sha, now
from .ai_sources import PublicSources, SOURCES, source_url
from memorive_language.policy import freeze as freeze_language, from_settings, validate as validate_language
from memorive_language.text import choose

METHODS=frozenset({'ai.state','ai.config_get','ai.config_save','ai.generate','ai.get','ai.cancel','ai.retry','ai.open_source'})
from .ai_prompt import VERSION as TEMPLATE
ZONE=timezone(timedelta(hours=9))
DEFAULT_CONFIG={'profile_ref':None,'sources':list(SOURCES),'range_mode':'7','range_start':None,'range_end':None,'focus':''}
STATEMENT={'anyOf':[{'type':'null'},{'type':'object','additionalProperties':False,'properties':{
    'text':{'type':'string'},'source_ids':{'type':'array','minItems':1,'items':{'type':'string'}}},'required':['text','source_ids']}]}
SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'mainline':STATEMENT,'trend':STATEMENT,'items':{'type':'array','maxItems':6,'items':{'type':'object','additionalProperties':False,
    'properties':{'title':{'type':'string'},'what':{'type':'string'},'why':{'type':'string'},
        'source_ids':{'type':'array','minItems':1,'items':{'type':'string'}}},'required':['title','what','why','source_ids']}}},
    'required':['mainline','items','trend']}

def workspace_settings(api):
    path=api._profile_path('WORKSPACE')/'research_workspace/research_workspace_v1.sqlite3'
    if not path.is_file():return {}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT json_extract(body,'$.profile_ref'),json_extract(body,'$.default_project') FROM objects WHERE kind='settings' AND id='default'").fetchone()
    return {'profile_ref':row[0],'project':row[1]} if row else {}

def period(start=None,end=None,days=7):
    today=datetime.now(ZONE).date()
    if start is None and end is None:
        if days not in {7,30}:raise ValueError('AI_RANGE_INVALID')
        end=today;start=end-timedelta(days=days-1)
    else:
        try:start=date.fromisoformat(start);end=date.fromisoformat(end)
        except (ValueError,TypeError):raise ValueError('AI_RANGE_INVALID') from None
    if start>end or end>today or (end-start).days>365:raise ValueError('AI_RANGE_INVALID')
    begin=datetime.combine(start,datetime.min.time(),ZONE).astimezone(timezone.utc)
    finish=datetime.combine(end+timedelta(days=1),datetime.min.time(),ZONE).astimezone(timezone.utc)
    return {'start':start.isoformat(),'end':end.isoformat(),'zone':'UTC+09:00','utc_start':begin.isoformat(),'utc_end':finish.isoformat()}

def briefing_title(language):
    return choose(language,'AI 近况','AI updates','AI の近況')

def provider_name(row):
    return {'openai_news':'OpenAI','deepmind_news':'Google DeepMind','claude_code_releases':'Claude Code'}.get(row['source'],row['source_name'])

def mapped_report(response,collection,window,style,language_context=None):
    language=validate_language(language_context or freeze_language())
    def t(zh,en,ja):return choose(language,zh,en,ja)
    if not isinstance(response,dict) or set(response)!={'mainline','items','trend'}:
        raise ValueError('AI_REPORT_SCHEMA_INVALID')
    rows=collection['materials'];allowed={r['source_id']:r for r in rows if r['body']}
    items=response['items']
    if not isinstance(items,list) or not 1<=len(items)<=6:raise ValueError('AI_REPORT_ITEMS_INVALID')
    text=[f"# {briefing_title(language)} · {window['start']}—{window['end']}",'',t('公开资料','Public sources','公開資料')+' · '+window['zone']+' · '+t('取得时间见各条出处','Retrieval times appear with each source','取得時刻は各出典に記載'),'']
    used=[]
    def statement(value):
        if value is None:return None
        if not isinstance(value,dict) or set(value)!={'text','source_ids'} or not isinstance(value['text'],str) or not value['text'].strip() or len(value['text'])>4000:
            raise ValueError('AI_REPORT_STATEMENT_INVALID')
        ids=value['source_ids']
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in allowed for i in ids):raise ValueError('AI_REPORT_CITATION_INVALID')
        if re.search(r'https?://|www\.',value['text'],re.I):raise ValueError('AI_REPORT_MODEL_URL_FORBIDDEN')
        used.extend(ids)
        links=' '.join('['+provider_name(allowed[i])+']('+source_url(allowed[i]['source'],allowed[i]['url'])+')' for i in dict.fromkeys(ids))
        return value['text'].strip()+' '+links
    mainline=statement(response['mainline']);trend=statement(response['trend'])
    if mainline:text.extend([mainline,''])
    for item in items:
        if not isinstance(item,dict) or set(item)!={'title','what','why','source_ids'}:raise ValueError('AI_REPORT_SCHEMA_INVALID')
        if any(not isinstance(item[k],str) or not item[k].strip() or len(item[k])>4000 for k in ('title','what','why')):
            raise ValueError('AI_REPORT_TEXT_INVALID')
        ids=item['source_ids']
        if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(s not in allowed for s in ids):
            raise ValueError('AI_REPORT_CITATION_INVALID')
        if any(re.search(r'https?://|www\.',item[k],re.I) for k in ('title','what','why')) :
            raise ValueError('AI_REPORT_MODEL_URL_FORBIDDEN')
        # All rendered links come from immutable public source records, never the model.
        text.extend(['## '+str(items.index(item)+1)+'. '+item['title'].strip(),'',item['what'].strip(),'',item['why'].strip(),''])
        for identity in ids:
            row=allowed[identity];url=source_url(row['source'],row['url'])
            label=row['title'].replace('[','（').replace(']','）').replace('\n',' ')
            provider=provider_name(row)
            event=t('事件日期未单独确认','Event date not independently confirmed','出来事の発生日は別途確認されていません')
            extent=t({'feed_summary':'来源摘要','feed_summary_excerpt':'来源摘要节选','release_notes':'发布记录','release_notes_excerpt':'发布记录节选','article_excerpt':'文章节选','article_main_text':'文章正文','title_only':'仅标题'},
                     {'feed_summary':'feed summary','feed_summary_excerpt':'feed summary excerpt','release_notes':'release notes','release_notes_excerpt':'release notes excerpt','article_excerpt':'article excerpt','article_main_text':'article text','title_only':'title only'},
                     {'feed_summary':'配信の要旨','feed_summary_excerpt':'配信要旨の抜粋','release_notes':'リリースノート','release_notes_excerpt':'リリースノートの抜粋','article_excerpt':'記事の抜粋','article_main_text':'記事本文','title_only':'タイトルのみ'}).get(row['body_extent'],row['body_extent'])
            text.append(t('出处：','Source: ','出典：')+f"[{label}]({url}) · {provider} · "+t('发布 ','Published ','公開 ')+str(row['published_at'])+' · '+event+' · '+t('取得 ','Retrieved ','取得 ')+row['retrieved_at']+' · '+extent)
            used.append(identity)
        text.append('')
    if trend:text.extend([trend,''])
    if collection['failures']:text.extend([t('部分来源读取失败；本次仅依据已取得的公开资料。','Some sources could not be read; this report uses only retrieved public material.','一部のソースを読み取れなかったため、取得できた公開資料だけに基づいています。'),''])
    return {'schema_version':TEMPLATE,'kind':'ai_briefing','period':None,'window':window,'style':style,'language_context':language,
        'markdown':'\n'.join(text),'items':items,'mainline':response['mainline'],'trend':response['trend'],'sources':rows,
        'source_status':collection['sources'],'source_failures':collection['failures'],
        'unknown_date_count':collection['unknown_date_count'],'used_source_ids':sorted(set(used)),
        'status':'PARTIAL' if collection['failures'] else 'SUCCEEDED',
        'citation_validation':'SOURCE_ID_AND_URL_BINDING_ONLY','claim_truth_validation':'REQUIRES_CONTENT_REVIEW'}

class AIBriefing:
    def __init__(self,runtime):
        self.r=runtime;self.sources=PublicSources(runtime.root)
    def config(self):
        p=self.r.root/'ai_briefing_settings.json'
        value=read(p) if p.exists() else {'revision':0,'config':{}}
        # Read-only migration of the early E7 shape; existing settings are preserved.
        return dict(value,config={**DEFAULT_CONFIG,**value['config']})
    def config_get(self):
        from .report_models import catalog
        value=self.config();options=catalog(self.r);ref=value['config']['profile_ref'] or workspace_settings(self.r.api).get('profile_ref')
        model=next((r for r in options if r['profile_ref']==ref and r['eligible']),None)
        cfg=value['config'];window=period(cfg['range_start'],cfg['range_end']) if cfg['range_mode']=='custom' else period(days=int(cfg['range_mode']))
        return dict(value,model_options=options,effective_model=model,sources=self.sources.cached(),inherits_chat_model=value['config']['profile_ref'] is None,default_window=period(),effective_window=window)
    def save(self,config,expected_revision):
        if not isinstance(config,dict) or set(config)!=set(DEFAULT_CONFIG):raise ValueError('AI_SETTINGS_INVALID')
        if config['range_mode'] not in {'7','30','custom'} or not isinstance(config['focus'],str) or len(config['focus'])>500:raise ValueError('AI_SETTINGS_INVALID')
        if config['range_mode']=='custom':period(config['range_start'],config['range_end'])
        elif config['range_start'] is not None or config['range_end'] is not None:raise ValueError('AI_SETTINGS_INVALID')
        if not isinstance(config['sources'],list) or len(set(config['sources']))!=len(config['sources']) or any(s not in SOURCES for s in config['sources']):raise ValueError('AI_SETTINGS_INVALID')
        options=self.config_get()['model_options'];ref=config['profile_ref']
        if ref is not None and not any(o['profile_ref']==ref and o['eligible'] for o in options):raise ValueError('AI_MODEL_UNAVAILABLE')
        with self.r.lock:
            if type(expected_revision) is not int or self.config()['revision']!=expected_revision:raise ValueError('AI_SETTINGS_REVISION_CONFLICT')
            write(self.r.root/'ai_briefing_settings.json',sealed({'revision':expected_revision+1,'config':config}))
        return self.config_get()
    def runs(self):return [s for s in self.r.state()['runs'] if s['kind']=='ai_briefing']
    def get(self,run_id):
        state=self.r.call('research.get',{'run_id':run_id})
        if state['kind']!='ai_briefing':raise ValueError('AI_RUN_INVALID')
        if state.get('reused_run_id'):
            original=self.r.call('research.get',{'run_id':state['reused_run_id']});state['result']=original.get('result')
            state['delivery_recoverable']=bool(state['result'])
        return state
    def state(self):
        rows=self.runs();latest=next((r for r in rows if r.get('library_locator')),None)
        return dict(self.config_get(),runs=rows[:20],active_run=next((r for r in rows if r['status'] in {'QUEUED','RUNNING'}),None),
            latest=self.get(latest['run_id']) if latest else None,default_window=period(),automatic_updates=False)
    def call(self,method,p):
        if method in {'ai.state','ai.config_get'}:
            if p:raise ValueError('AI_PARAMS_INVALID')
            return self.state() if method=='ai.state' else self.config_get()
        if method=='ai.config_save':
            if set(p)!={'config','expected_revision'}:raise ValueError('AI_PARAMS_INVALID')
            return self.save(**p)
        if method=='ai.generate':
            if set(p)-{'request_id','start','end','days','focus','refresh','output_locale'}:raise ValueError('AI_PARAMS_INVALID')
            return self.start(**p)
        if method=='ai.retry':
            if set(p)!={'run_id','request_id'}:raise ValueError('AI_PARAMS_INVALID')
            old=self.get(p['run_id']);path=self.r._run_path(p['run_id'])
            if old['status'] in {'RUNNING','QUEUED','CANCELLED','SUCCEEDED'}:raise ValueError('AI_RETRY_UNAVAILABLE')
            if ((path/'result.json').exists() or old.get('reused_run_id')) and (old.get('failed_stage') in {'PUBLISHING','NOTIFYING'} or old.get('notification_error')):
                return self.r.repair_delivery(p['run_id'])
            request=read(path/'request.json')
            return self.start(request_id=p['request_id'],start=request['window']['start'],end=request['window']['end'],
                focus=request['focus'],retry_from=p['run_id'])
        if method=='ai.open_source':
            if set(p)!={'run_id','source_id','url'}:raise ValueError('AI_PARAMS_INVALID')
            result=self.get(p['run_id']).get('result') or {}
            row=next((r for r in result.get('sources',[]) if r['source_id']==p['source_id'] and r['url']==p['url']),None)
            if row is None:raise ValueError('AI_SOURCE_URL_INVALID')
            return self.r.api.assistant_launch_target({'target':source_url(row['source'],row['url'])})
        if set(p)!={'run_id'}:raise ValueError('AI_PARAMS_INVALID')
        self.get(p['run_id'])
        return self.get(p['run_id']) if method=='ai.get' else self.r.call('research.cancel',p)
    def start(self,request_id,start=None,end=None,days=None,focus=None,refresh=False,retry_from=None,output_locale=None):
        cfg=self.config_get()
        if focus is None:focus=cfg['config']['focus']
        if not isinstance(request_id,str) or not 1<=len(request_id)<=160 or not isinstance(focus,str) or len(focus)>500 or type(refresh) is not bool:
            raise ValueError('AI_PARAMS_INVALID')
        window=period(start,end,days if days is not None else 7) if start is not None or end is not None or days is not None else cfg['effective_window']
        model=cfg['effective_model']
        if not model:raise ValueError('AI_MODEL_REQUIRED')
        if not cfg['config']['sources']:raise ValueError('AI_SOURCE_REQUIRED')
        from memorive_settings import answer_styles
        pref=self.r.api._service.call('settings.user_get',{})
        style=answer_styles.snapshot(pref['config']['answer_style'],preference_revision=pref['revision'])
        language=from_settings(self.r.api._service,current_user_text=focus,explicit_locale=output_locale)
        # A failure retry belongs to the old output, even after a UI language change.
        # A fresh explicit generation snapshots the new setting. Legacy jobs were Chinese.
        if retry_from:
            old=read(self.r._run_path(retry_from)/'request.json')
            language=validate_language(old.get('language_context') or freeze_language())
        intent={'window':window,'sources':cfg['config']['sources'],'model_ref':model['profile_ref'],
            'model_binding':model['binding_hash'],'focus':focus.strip(),'style':style,'template':TEMPLATE,'language_context':language}
        identity='research-'+sha('ai:'+request_id)[:24];path=self.r._run_path(identity)
        with self.r.lock:
            if self.r.closed:raise ValueError('RESEARCH_CLOSED')
            if (path/'state.json').exists():
                prior=read(path/'request.json')
                # Replaying a request cannot silently acquire a new language.
                frozen_language=prior.get('language_context') or freeze_language()
                if output_locale is not None and output_locale!=frozen_language['effective_output_locale']:raise ValueError('AI_IDEMPOTENCY_CONFLICT')
                if 'language_context' in prior['intent']:intent['language_context']=validate_language(frozen_language)
                else:
                    intent.pop('language_context');intent['template']=prior['intent']['template']
                if prior['intent']!=intent or prior['refresh']!=refresh or prior.get('retry_from')!=retry_from:raise ValueError('AI_IDEMPOTENCY_CONFLICT')
                return self.get(identity)
            if self.r._has_active_run():
                active=read(self.r._run_path(self.r.current)/'request.json')
                if active.get('kind')=='ai_briefing' and active.get('intent')==intent:return self.get(self.r.current)
                raise ValueError('RESEARCH_ALREADY_RUNNING')
            frozen=self.r.api._service.call('settings.ai_briefing_freeze',{'run_id':identity,'profile_ref':model['profile_ref']})
            params={'kind':'ai_briefing','period':None,'config':{'push_enabled':True},'intent':intent,**intent,
                'refresh':refresh,'retry_from':retry_from,'report_model':frozen}
            write(path/'request.json',sealed(params))
            self.r.stop=threading.Event();self.r.current=identity
            item=self.r._state(identity,run_id=identity,kind='ai_briefing',period=None,title=f"{briefing_title(language)} · {window['start']}—{window['end']}",language_context=language,
                status='QUEUED',stage='WAITING',created_at=now(),request_sha256=sha(params),report_model=frozen,
                model_calls=0,paid_model_calls=0,window=window)
            self.r.thread=threading.Thread(target=self.execute,args=(identity,params),name='desktop-ai-briefing',daemon=True)
            self.r.thread.start();return item
    def _deliver_cached(self,run_id,check=lambda:None):
        """Complete this request against the original artifact; never rewrite its owner."""
        r=self.r;state=read(r._run_path(run_id)/'state.json');owner=state['reused_run_id']
        origin=r._run_path(owner);result=read(origin/'result.json')
        if (result.get('kind')!='ai_briefing' or result.get('cache_key')!=state['cache_key'] or
                result.get('sha256')!=state.get('result_sha256') or sealed(result)['sha256']!=result.get('sha256') or
                (origin/'result.md').read_text('utf8')!=result['markdown'] or
                state.get('library_locator')!='memorive://artifact/'+owner+'/result'):
            raise ValueError('AI_CACHED_RESULT_INVALID')
        check();r._state(run_id,stage='NOTIFYING')
        # The original artifact identity is also the message dedupe identity.
        # A prior cancellation remains recorded on its original task.
        mid=r._message(owner,'ai_briefing',result,None)
        check()
        return r._state(run_id,status=result['status'],stage='COMPLETE',finished_at=now(),
            message_id=mid,notification_status='MESSAGE_CREATED',notification_error=None,
            failed_stage=None,error_code=None,delivery_repaired_at=now())
    def repair_cached_delivery(self,run_id):
        with self.r.lock:
            state=read(self.r._run_path(run_id)/'state.json')
            if state['status'] in {'RUNNING','QUEUED'}:raise ValueError('RESEARCH_STILL_RUNNING')
            if state['status']=='CANCELLED':raise ValueError('AI_CANCELLED_DELIVERY_FORBIDDEN')
            self._deliver_cached(run_id)
            self.r._notify_ui();return self.get(run_id)
    def execute(self,run_id,params):
        from memorive_settings.task_scheduling import TASK_CATEGORIES
        from memorive_settings.call_ledger import execution_control,ExecutionControlSignal
        from memorive_workflow.node_progress import session_scope
        r=self.r;path=r._run_path(run_id)
        def check():
            if r.stop.is_set():raise ExecutionControlSignal('CANCELLED')
        try:
            with execution_control(check),TASK_CATEGORIES.enter('RESEARCH_AI_BRIEFING',checkpoint=check),session_scope(r._progress_session(run_id)):
                r._state(run_id,status='RUNNING',stage='COLLECTING')
                oldpath=r._run_path(params['retry_from']) if params.get('retry_from') else None
                prior=read(oldpath/'materials.json') if oldpath and (oldpath/'materials.json').exists() else None
                oldrequest=read(oldpath/'request.json') if prior else None
                # Retry retains its explicit dates/focus but snapshots current source/model/style settings.
                # Reuse source material only when its original scope still matches that new request.
                if prior and (oldrequest['window']!=params['window'] or set(oldrequest['sources'])!=set(params['sources']) or
                        any(row['source'] not in params['sources'] for group in ('materials','sources','failures') for row in prior[group])):
                    prior=None
                if prior and not prior['failures']:
                    collection={k:v for k,v in prior.items() if k!='sha256'}
                else:
                    collection=self.sources.collect(params['sources'],params['window']['utc_start'],params['window']['utc_end'],path,check,
                        refresh=params['refresh'] or bool(prior),only=[f['source'] for f in prior['failures']] if prior else None)
                write(path/'materials.json',sealed(collection));r._state(run_id,source_failures=collection['failures'])
                check()
                if collection['failures'] and len(collection['failures'])==len(params['sources']):raise ValueError('AI_ALL_SOURCES_FAILED')
                materials=[m for m in collection['materials'] if m['body'] and m['body_extent']!='title_only']
                if not materials:raise ValueError('AI_NO_DATED_MATERIALS')
                cache_key=sha({'intent':params['intent'],'versions':sorted(m['version'] for m in materials),'source_failures':collection['failures']})
                for previous in self.runs():
                    if previous['run_id']!=run_id and previous.get('cache_key')==cache_key and previous.get('library_locator'):
                        r._state(run_id,stage='NOTIFYING',cache_key=cache_key,
                            reused_run_id=previous.get('reused_run_id') or previous['run_id'],library_locator=previous['library_locator'],
                            result_sha256=previous['result_sha256'],cache_hit=True)
                        self._deliver_cached(run_id,check)
                        r._notify_ui();return
                r._state(run_id,stage='COMPOSING',cache_key=cache_key)
                from .ai_prompt import assemble
                prompt_record=assemble(materials,params['window'],params['focus'],params['style'],SCHEMA,params.get('language_context'))
                prompt=prompt_record['prompt']
                write(path/'model_request.json',sealed(dict(prompt_record,schema=SCHEMA,snapshot_id=params['report_model']['snapshot_id'],
                    scope='PUBLIC_MATERIALS_AND_EXPLICIT_FOCUS_ONLY',source_ids=[m['source_id'] for m in materials])))
                check()
                response=r.api._service.call('settings.ai_briefing_execute',{'snapshot_id':params['report_model']['snapshot_id'],'prompt':prompt,'response_schema':SCHEMA})
                write(path/'model_result.json',sealed(response));receipt=response.get('execution_receipt') or {}
                calls=len(receipt.get('physical_call_ids') or [])
                r._state(run_id,model_calls=calls,paid_model_calls=calls if params['report_model']['profile_kind']=='API' else 0,execution_receipt=receipt)
                from memorive_research_workspace.model_receipts import verified_result
                verified_result(response,params['report_model']);check()
                r._state(run_id,stage='VALIDATING')
                result=mapped_report(response.get('response'),collection,params['window'],params['style'],params.get('language_context'))
                result.update(report_model=params['report_model'],execution_receipt=receipt,model_calls=calls,
                    paid_model_calls=calls if params['report_model']['profile_kind']=='API' else 0,cache_key=cache_key)
                result=sealed(result);write(path/'result.json',result)
                (path/'result.md').write_text(result['markdown'],encoding='utf8')
                check();r._state(run_id,stage='PUBLISHING',result_sha256=result['sha256'])
                r._publish_library(run_id,'ai_briefing',result)
                check();r._state(run_id,stage='NOTIFYING')
                mid=r._message(run_id,'ai_briefing',result,None)
                r._state(run_id,status=result['status'],stage='COMPLETE',finished_at=now(),message_id=mid,notification_status='MESSAGE_CREATED')
        except (Exception,ExecutionControlSignal) as exc:
            previous=read(path/'state.json');code=str(exc)
            if not re.fullmatch('[A-Z0-9_: -]{1,180}',code):code='AI_EXECUTION_FAILED'
            stopped='INTERRUPTED' if r.closed else 'CANCELLED' if r.stop.is_set() else 'FAILED'
            r._state(run_id,status=stopped,stage='STOPPED',failed_stage=previous.get('stage'),error_code=code,finished_at=now())
        r._notify_ui()
