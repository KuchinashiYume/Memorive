"""Console-session-only renderer, reusing the existing native pet surface.

No preference writes, delivery receipts, business events, or arbitrary scripts
are accepted. UI work is bounded and never runs with the bridge/product lock held.
"""
import json
import threading
import time
import uuid

class ConsoleExpressionRenderer:
    def __init__(self,window,assistant,api,overlays):
        self.window,self.assistant,self.api,self.overlays=window,assistant,api,overlays
        if not getattr(api,'_console_bridge',None) or api._system_effects_enabled:
            raise ValueError('EXPRESSION_CONSOLE_SESSION_REQUIRED')
    def _ui(self,fn):
        from System import Action
        form=self.assistant.native
        done=threading.Event();expired=threading.Event();result={}
        def action():
            if expired.is_set():done.set();return
            try:result['value']=fn()
            except Exception as exc:result['error']=exc
            finally:done.set()
        form.BeginInvoke(Action(action))
        if not done.wait(2.5):
            expired.set()
            raise ValueError('EXPRESSION_UI_TIMEOUT')
        if 'error' in result:raise result['error']
        return result.get('value')
    def _overlay(self):
        value=self.overlays.get(int(self.assistant.native.Handle.ToInt64()))
        if not value:raise ValueError('EXPRESSION_RENDERER_NOT_READY')
        return value
    def begin(self,event,cancel):
        if cancel.is_set():raise ValueError('EXPRESSION_CANCELLED_BEFORE_RENDER')
        hidden=self.window.evaluate_js("document.documentElement.classList.contains('desktop-hide-expressions')")
        if hidden:raise ValueError('EXPRESSION_HIDDEN_BY_PREFERENCE')
        lease={'event':event,'id':'preview-'+uuid.uuid4().hex,'native':event['target'].startswith('native')}
        try:
            if not lease['native']:
                # In-page preview only: do not navigate, fire save/error observers
                # or pretend that a real page/business event has occurred.
                spec=json.dumps({'id':lease['id'],'asset':event['asset'],'label':event['label'],'event':event['event_id']})
                result=self.window.evaluate_js("""(()=>{const s="""+spec+""";
                  if(document.querySelector('[data-console-expression]'))return false;
                  const panel=document.createElement('section');panel.dataset.consoleExpression=s.id;
                  panel.style.cssText='position:fixed;right:28px;bottom:28px;z-index:2147483000;padding:16px;border:1px solid #ded7cc;border-radius:16px;background:#fffdf9;box-shadow:0 8px 28px #493c2522;pointer-events:none';
                  const title=document.createElement('div'),img=document.createElement('img'),visual=document.createElement('div');
                  const raw=document.documentElement.lang||'zh-CN',lang=raw.startsWith('en')?'en-US':raw.startsWith('ja')?'ja-JP':'zh-CN';
                  title.textContent=(s.label[lang]||s.label['zh-CN'])+' · '+({'zh-CN':'预览','en-US':'Preview','ja-JP':'プレビュー'}[lang]||'Preview');
                  title.style.cssText='font:16px "Times New Roman",serif;color:#706b62;margin-bottom:8px';
                  img.src='assets/illustrations/'+s.asset+'.svg';img.alt='';
                  img.style.cssText='display:block;width:200px;height:200px;object-fit:contain';
                  visual.dataset.consoleVisual='';visual.append(img);panel.append(title,visual);panel.dataset.currentAsset=s.asset;
                  document.body.append(panel);panel.__consoleTimers=[];
                  const later=(f,ms)=>panel.__consoleTimers.push(setTimeout(f,ms));
                  if(s.event==='animation.home-question'){
                    visual.className='desktop-home-expression-button';visual.style.width='200px';
                    visual.style.transition='transform 260ms cubic-bezier(.2,.8,.2,1)';
                    later(()=>{visual.dataset.expression='question';img.src='assets/illustrations/question.svg';panel.dataset.currentAsset='question';},120);
                    later(()=>{visual.dataset.expression='interaction';img.src='assets/illustrations/wink.svg';panel.dataset.currentAsset='wink';},2120);
                  }else if(s.event==='animation.logo-compress'){
                    later(()=>{visual.style.animation='desktop-logo-compress 340ms cubic-bezier(.22,.75,.22,1)';},120);
                  }else if(s.event==='animation.logo-shake'){
                    later(()=>{visual.style.animation='desktop-logo-shake 360ms ease-out';},120);
                  }
                  return true;})()""")
                if result is not True:raise ValueError('EXPRESSION_PREVIEW_BUSY')
                lease['web_created']=True
                deadline=time.monotonic()+2
                while time.monotonic()<deadline:
                    if cancel.is_set():raise ValueError('EXPRESSION_CANCELLED_BEFORE_RENDER')
                    if self.observe(lease)['visible']:return lease
                    time.sleep(.05)
                raise ValueError('EXPRESSION_ASSET_DECODE_TIMEOUT')
            prefs=self.api.call('assistant.preference_get',{})['preferences']
            if not prefs.get('enabled',True):raise ValueError('EXPRESSION_ASSISTANT_DISABLED')
            runtime=self.assistant.evaluate_js('window.__Desktop_ASSISTANT_RUNTIME__?.inspect()')
            # The native overlay may be ready before the web business bootstrap.
            # Only the timer API is needed here; actual native readiness is
            # checked on the UI thread below, not inferred from a web data flag.
            if not runtime or type(runtime.get('auto_refresh_enabled')) is not bool:
                raise ValueError('EXPRESSION_RENDERER_NOT_READY')
            lease['refresh']=runtime['auto_refresh_enabled']
            self.assistant.evaluate_js('window.__Desktop_ASSISTANT_RUNTIME__.setAutoRefreshEnabled(false)')
            lease['refresh_paused']=True
            def start():
                state=self._overlay()
                if not self.assistant.native.Visible:raise ValueError('EXPRESSION_ASSISTANT_NOT_VISIBLE')
                if state['animation_locked'] or state['presentation_queue'] or state.get('drag') or state.get('menu_open'):
                    raise ValueError('EXPRESSION_NATIVE_BUSY')
                keys=('base_frame','animation_state','animation_source','animation_sequence','reduced_motion',
                      'current_frame','presentation_reason_code','last_presentation_signature')
                lease['before']={key:state.get(key) for key in keys}
                lease['position']=(self.assistant.native.Left,self.assistant.native.Top)
                lease['auto_enabled']=bool(state['auto_blink_timer'].Enabled)
                lease['native_started']=True
                state['auto_blink_timer'].Stop()
                state['reduced_motion']=False
                state['presentation_reason_code']=event.get('reason_code','CONSOLE_EXPRESSION_PREVIEW')
                if event['target']=='native_frame':
                    state['set_frame'](event['frame'])
                else:
                    from assistant_native_motion import base_frame_for_presentation
                    state['play_native_motion'](event['motion'],source_name='console-preview',
                        base_frame=base_frame_for_presentation(event['motion'],event['reason_code']))
                # Static paths schedule a blink; the probe owns the brief interval.
                state['auto_blink_timer'].Stop()
            self._ui(start)
            return lease
        except Exception:
            try:restored=self.end(lease)
            except Exception:raise ValueError('EXPRESSION_RESTORE_FAILED') from None
            if not restored:raise ValueError('EXPRESSION_RESTORE_FAILED')
            raise
    def observe(self,lease):
        if not lease['native']:
            spec=json.dumps(lease['id'])
            row=self.window.evaluate_js("""(()=>{const id="""+spec+""";const p=[...document.querySelectorAll('[data-console-expression]')].find(x=>x.dataset.consoleExpression===id),i=p?.querySelector('img'),v=p?.querySelector('[data-console-visual]');return {visible:Boolean(p&&i?.complete&&i?.naturalWidth&&!document.documentElement.classList.contains('desktop-hide-expressions')),decoded:Boolean(i?.naturalWidth),transform:v?getComputedStyle(v).transform:null,current_asset:p?.dataset.currentAsset,animation_playing:Boolean(v?.getAnimations().some(a=>a.playState==='running'))};})()""")
            return {**row,'asset':lease['event']['asset']}
        def sample():
            state=self._overlay()
            if state['last_presentation_signature']!=lease['before']['last_presentation_signature']:
                lease['interrupted']=True
                raise ValueError('EXPRESSION_INTERRUPTED_BY_PRODUCT')
            state['auto_blink_timer'].Stop()
            return {'visible':bool(self.assistant.native.Visible),'frame':state['current_frame'],
                'frame_sha256':state['current_frame_sha256'],'animation':state['animation_state'],
                'transform':list(state.get('current_transform') or ()),
                'animation_locked':bool(state['animation_locked']),
                'position_unchanged':(self.assistant.native.Left,self.assistant.native.Top)==lease['position'],
                'render_cache_count':len(state['render_cache']),'render_cache_limit':state['render_cache_limit']}
        return self._ui(sample)
    def end(self,lease):
        restored=True
        if lease.get('web_created'):
            spec=json.dumps(lease['id'])
            restored=self.window.evaluate_js("""(()=>{const id="""+spec+""";const p=[...document.querySelectorAll('[data-console-expression]')].find(x=>x.dataset.consoleExpression===id);p?.__consoleTimers?.forEach(clearTimeout);p?.remove();return !document.querySelector('[data-console-expression]');})()""") is True
        if lease.get('native_started'):
            def restore():
                state=self._overlay()
                # A real incoming presentation takes precedence; never replay or
                # overwrite its delivery state while ending a synthetic preview.
                if state['last_presentation_signature']==lease['before']['last_presentation_signature']:
                    state['animation_timer'].Stop();state['auto_blink_timer'].Stop()
                    state.update(lease['before']);state['animation_locked']=False
                    state['set_frame'](lease['before']['current_frame'])
                    if lease['auto_enabled']:state['schedule_auto_blink']()
                return True
            try:restored=bool(self._ui(restore)) and restored
            finally:
                if lease.get('refresh_paused'):
                    self.assistant.evaluate_js('window.__Desktop_ASSISTANT_RUNTIME__.setAutoRefreshEnabled('+json.dumps(lease['refresh'])+')')
                    lease['refresh_paused']=False
        if lease.get('refresh_paused'):
            self.assistant.evaluate_js('window.__Desktop_ASSISTANT_RUNTIME__.setAutoRefreshEnabled('+json.dumps(lease['refresh'])+')')
        return restored
