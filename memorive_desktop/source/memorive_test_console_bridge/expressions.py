"""Ephemeral visual probes. No business stores, filesystem writes or model calls."""
from collections import deque
from copy import deepcopy
from datetime import datetime,timezone
import threading
import time
import uuid

SCHEMA='Memorive-ExpressionConsole-v1'
LABELS={
 'neutral':('睁眼','Open eyes','開眼'),'writing':('完成','Completed','完了'),
 'question':('疑问','Question','疑問'),'wink':('互动','Interaction','交流'),
 'shovel':('施工','Working','作業中'),'one_eye':('常驻','Idle','待機'),
 'injury':('报错','Error','エラー'),'sleepy':('闭眼','Closed eyes','閉眼'),
 'empty':('无文件','No files','ファイルなし'),'success':('确认','Confirmed','確認'),
}
FRAMES={'idle':'one_eye','blink-open':'neutral','blink-closed':'sleepy','interaction':'wink',
        'working':'shovel','success':'writing','save-success':'success','error':'injury'}
MOTIONS=[
 ('idle','STATIC','NO_ACTIVE_REAL_JOB',0,('静态待机','Static idle','静止待機')),
 ('drag','DRAG','USER_DRAG',0,('拖动表情','Drag expression','ドラッグ表情')),
 ('sleep','SLEEP','IDLE_SLEEP',0,('休眠','Sleep','休眠')),
 ('blink','BLINK','AUTO_BLINK',3000,('眨眼','Blink','まばたき')),
 ('interaction','INTERACTION','USER_INTERACTION',1100,('点击互动','Interaction','クリック反応')),
 ('wake','WAKE','USER_WAKE',900,('唤醒','Wake','起床')),
 ('message','MESSAGE','MESSAGE_READY',700,('消息提示','Message cue','メッセージ通知')),
 ('working','WORKING','JOB_RUNNING',900,('工作中','Working','作業中')),
 ('attention','ATTENTION','HUMAN_REVIEW',1100,('待确认','Attention','確認待ち')),
 ('success','SUCCESS','JOB_TERMINAL_SUCCESS',1400,('任务完成','Task complete','タスク完了')),
 ('save-success','SUCCESS','LOCAL_UI_SAVE_SUCCESS',1400,('保存成功','Saved','保存完了')),
 ('error','ERROR','LOCAL_UI_ERROR',900,('错误提示','Error cue','エラー通知')),
]
WEB_ANIMATIONS=[
 ('home-question','wink',2460,('主页歪头','Home tilt','ホームの首かしげ')),
 ('logo-compress','one_eye',460,('Logo 按压','Logo press','ロゴの押下')),
 ('logo-shake','one_eye',480,('Logo 晃动','Logo shake','ロゴの揺れ')),
]
def label(names):return dict(zip(('zh-CN','en-US','ja-JP'),names))
EVENTS=tuple(
 [{'event_id':'asset.'+key,'target':'web','asset':key,'label':label(names),'motion_ms':0} for key,names in LABELS.items()]
 +[{'event_id':'frame.'+key,'target':'native_frame','frame':key,'asset':asset,'label':label(LABELS[asset]),'motion_ms':0} for key,asset in FRAMES.items()]
 +[{'event_id':'motion.'+key,'target':'native_motion','motion':motion,'reason_code':reason,'motion_ms':duration,'label':label(names)} for key,motion,reason,duration,names in MOTIONS]
 +[{'event_id':'animation.'+key,'target':'web_animation','asset':asset,'motion_ms':duration,'label':label(names)} for key,asset,duration,names in WEB_ANIMATIONS])
for row in EVENTS:
    row['animated']=row['motion_ms']>0
    row['group']='web_animation' if row['target']=='web_animation' else 'native_animation' if row['animated'] else 'native_pose' if row['target'].startswith('native') else 'static_asset'
BY_ID={row['event_id']:row for row in EVENTS}
def catalog():
    return {'schema_version':SCHEMA,'events':deepcopy(EVENTS),'duration_ms':{'min':500,'max':5000,'default':1800,'integer_only':True},
            'one_probe_at_a_time':True,'preview_only':True,'business_event_injected':False,
            'model_calls_allowed':False,'history_limit':64,'poll_interval_ms':250,
            'web_semantics':'ISOLATED_ASSET_PREVIEW','native_semantics':'EXISTING_NATIVE_RENDERER',
            'duration_rule':'max(duration_ms, motion_ms + 250)',
            'motion_preview':'EXPLICIT_FULL_MOTION_NO_PREFERENCE_WRITE',
            'drag_preview_moves_window':False}
def utc():return datetime.now(timezone.utc).isoformat()
def safe_code(error):
    import re
    text=str(error)
    return text if re.fullmatch('EXPRESSION_[A-Z0-9_]{1,80}',text) else 'EXPRESSION_RENDER_FAILED'

class ExpressionController:
    def __init__(self,session_id,stop):
        self.session_id,self.stop=session_id,stop
        self.lock=threading.RLock()
        self.renderer=None
        self.active=None
        self.worker=None
        self.cancel_event=threading.Event()
        self.history=deque(maxlen=64)
        self.events=deque(maxlen=128)
        self.sequence=0
    def bind(self,renderer):
        with self.lock:
            if self.renderer is not None:raise ValueError('EXPRESSION_RENDERER_ALREADY_BOUND')
            self.renderer=renderer
    def _event(self,state):
        self.sequence+=1
        self.events.append({'sequence':self.sequence,'probe_id':self.active['probe_id'],'state':state,'occurred_at':utc()})
    def state(self):
        # Cached only; do not call the UI, service, disk or heavy product snapshot.
        with self.lock:
            return deepcopy({'schema_version':SCHEMA,'session_id':self.session_id,
                'renderer_ready':self.renderer is not None,'closing':self.stop.is_set(),
                'active':self.active,'history':list(self.history),'events':list(self.events),
                'last_sequence':self.sequence,'preview_only':True,'business_event_injected':False})
    def trigger(self,params,request_id):
        event=BY_ID[params['event_id']]
        with self.lock:
            if self.stop.is_set():raise ValueError('EXPRESSION_SESSION_CLOSING')
            if self.renderer is None:raise ValueError('EXPRESSION_RENDERER_NOT_READY')
            if self.active is not None:raise ValueError('EXPRESSION_BUSY')
            self.cancel_event=threading.Event()
            self.active={'probe_id':'expression-'+uuid.uuid4().hex,'request_id':request_id,
                'event_id':event['event_id'],'target':event['target'],'state':'ACCEPTED',
                'duration_ms':max(params['duration_ms'],event['motion_ms']+250),
                'accepted_at':utc(),'displayed':False,'restored':False,'samples':[]}
            self._event('ACCEPTED')
            response=deepcopy(self.active)
            self.worker=threading.Thread(target=self._run,args=(deepcopy(event),),daemon=True,name='console-expression')
            self.worker.start()
            return {'probe':response,'completed':False,'preview_only':True}
    def cancel(self):
        with self.lock:
            self.cancel_event.set()
            return {'cancel_requested':self.active is not None,'probe_id':self.active['probe_id'] if self.active else None,
                    'restoration_pending':self.active is not None}
    def close(self,timeout=4.0):
        self.cancel()
        worker=self.worker
        if worker and worker is not threading.current_thread():worker.join(timeout)
        return not (worker and worker.is_alive())
    def _run(self,event):
        terminal='COMPLETED';error=None;lease=None;restored=False
        try:
            if self.cancel_event.is_set() or self.stop.is_set():terminal='CANCELLED';return
            lease=self.renderer.begin(event,self.cancel_event)
            sample=self.renderer.observe(lease)
            if not sample.get('visible'):raise ValueError('EXPRESSION_NOT_VISIBLE')
            with self.lock:
                self.active.update(state='DISPLAYED',displayed=True,displayed_at=utc())
                self.active['samples'].append(sample);self._event('DISPLAYED')
                duration=self.active['duration_ms']/1000
            until=time.monotonic()+duration
            while time.monotonic()<until:
                if self.cancel_event.wait(min(.1,max(0,until-time.monotonic()))) or self.stop.is_set():
                    terminal='CANCELLED';break
                sample=self.renderer.observe(lease)
                with self.lock:
                    if len(self.active['samples'])<64:self.active['samples'].append(sample)
        except Exception as exc:
            error=safe_code(exc)
            if error=='EXPRESSION_CANCELLED_BEFORE_RENDER' and self.cancel_event.is_set():
                terminal='CANCELLED';error=None
            else:terminal='FAILED'
        finally:
            with self.lock:self.active['state']='RESTORING';self._event('RESTORING')
            try:
                restored=(error!='EXPRESSION_RESTORE_FAILED') if lease is None else self.renderer.end(lease)
                if not restored:terminal='FAILED';error='EXPRESSION_RESTORE_FAILED'
            except Exception:
                terminal='FAILED';error='EXPRESSION_RESTORE_FAILED'
            with self.lock:
                self.active.update(state=terminal,restored=restored,completed_at=utc(),error_code=error)
                self._event(terminal);self.history.append(deepcopy(self.active));self.active=None
