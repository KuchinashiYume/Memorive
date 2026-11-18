/* Closing the window: the dialog that asks whether to quit or keep running in the tray when tasks are running or
   settings are not saved. product/desktop/window_chrome.py calls window.__Desktop_WINDOW_CHROME__.requestClose(). */
(() => {
  'use strict';
  if (window.__Desktop_WINDOW_CHROME__) return;
  const api=()=>window.pywebview?.api;
  // The texts are the window.* messages in shared/frame/messages.js.
  const WORDS=['title','cancel','exit','discard','tray','x','min','max','restore','close','busy','loading','unavailable','retry','dirty','running','background'];
  let nativeState=null,dialog=null,view=null,phase='idle',sequence=0,returnFocus=null,geometryFrame=0;
  const words=()=>Object.fromEntries(WORDS.map(word=>[word,window.__Desktop_I18N__.text(`window.${word}`)]));
  const edit=()=>window.__Desktop_SETTINGS_BRIDGE__?.getEditState?.()||{ready:false,dirty:false,saving:false};
  function node(tag,cls,text) {
    const e=document.createElement(tag);
    if(cls)e.className=cls;
    if(text)e.textContent=text;
    return e;
  }
  const glyphs={min:'M5 12h14',max:'M5 5h14v14H5z',restore:'M8 4h12v12M4 8h12v12H4z',close:'m6 6 12 12M18 6 6 18'};
  function icon(kind) {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24');
    svg.setAttribute('aria-hidden','true');
    const p=document.createElementNS(svg.namespaceURI,'path');
    p.setAttribute('d',glyphs[kind]);
    svg.append(p);
    return svg;
  }
  function button(action,cls='') {
    const b=node('button',cls);
    b.type='button';
    b.dataset.windowAction=action;
    return b;
  }
  function updateCopy() {
    const c=words();
    for(const b of document.querySelectorAll('.desktop-window-control')){
      const key=b.dataset.windowAction;
      const label=c[key==='max'&&nativeState?.maximized?'restore':key];
      b.setAttribute('aria-label',label);
      b.title=label;
      if(key==='max')b.replaceChildren(icon(nativeState?.maximized?'restore':'max'));
    }
    if(!dialog)return;
    dialog.querySelector('h2').textContent=c.title;
    dialog.querySelector('[data-window-action=x]').setAttribute('aria-label',c.x);
    dialog.querySelector('[data-window-action=x]').title=c.x;
    dialog.querySelector('[data-window-action=cancel]').textContent=c.cancel;
    dialog.querySelector('[data-window-action=exit]').textContent=view?.edit?.dirty?c.discard:c.exit;
    dialog.querySelector('[data-window-action=tray]').textContent=c.tray;
    dialog.querySelector('[data-window-action=retry]').textContent=c.retry;
    const notice=dialog.querySelector('.desktop-close-notice'), messages=[];
    if(phase==='error')messages.push(c.unavailable);
    else if(phase==='checking')messages.push(c.loading);
    else if(phase==='committing'||view?.edit?.saving)messages.push(c.busy);
    else if(view&&!view.edit.ready)messages.push(c.loading);
    else if(view){
      if(view.active_job_count)messages.push(c.running.replace('{n}',view.active_job_count));
      if(view.edit.dirty)messages.push(c.dirty);
      if(!view.tray_allowed)messages.push(c.background);
    }
    notice.textContent=messages.join(' ');
    notice.hidden=!messages.length;
    const blocked=phase!=='ready'||!view?.edit.ready||view.edit.saving;
    dialog.querySelector('[data-window-action=exit]').disabled=blocked;
    dialog.querySelector('[data-window-action=tray]').disabled=blocked||!view?.tray_allowed;
    dialog.querySelector('[data-window-action=x]').disabled=phase==='committing';
    dialog.querySelector('[data-window-action=cancel]').disabled=phase==='committing';
    dialog.querySelector('[data-window-action=retry]').hidden=phase!=='error'&&!(phase==='ready'&&(!view?.edit.ready||view.edit.saving));
  }
  function ensureDialog() {
    if(dialog)return;
    dialog=node('dialog','desktop-close-dialog');
    dialog.id='desktop-close-dialog';
    dialog.dataset.desktopNoTranslate='true';
    dialog.setAttribute('aria-labelledby','desktop-close-title');
    dialog.setAttribute('aria-describedby','desktop-close-notice');
    const header=node('header','desktop-close-header'),title=node('h2');
    title.id='desktop-close-title';
    const x=button('x','desktop-close-x');
    x.append(icon('close'));
    x.addEventListener('click',cancel);
    header.append(title,x);
    const notice=node('p','desktop-close-notice');
    notice.id='desktop-close-notice';
    notice.setAttribute('role','status');
    const footer=node('footer','desktop-close-actions');
    const back=button('cancel','desktop-close-button');
    back.addEventListener('click',cancel);
    const actions=node('div','desktop-close-choices'),retry=button('retry','desktop-close-button');
    retry.hidden=true;
    retry.addEventListener('click',()=>void prepare(true));
    const exit=button('exit','desktop-close-button');
    exit.addEventListener('click',()=>void choose('EXIT'));
    const tray=button('tray','desktop-close-button desktop-close-primary');
    tray.addEventListener('click',()=>void choose('HIDE'));
    actions.append(retry,exit,tray);
    footer.append(back,actions);
    dialog.append(header,notice,footer);
    dialog.addEventListener('cancel',event=>{
      event.preventDefault();
      cancel();
    });
    document.body.append(dialog);
    updateCopy();
  }
  function show() {
    ensureDialog();
    updateCopy();
    if(!dialog.open){
      dialog.showModal();
      dialog.querySelector('[data-window-action=cancel]').focus();
    }
    scheduleGeometry();
  }
  function dismiss() {
    dialog?.close();
    phase='idle';
    view=null;
    scheduleGeometry();
    if(returnFocus?.isConnected&&!returnFocus.closest('[hidden]'))returnFocus.focus({preventScroll:true});
  }
  function cancel() {
    if(phase==='committing')return; // A committed exit cannot be undone.
    sequence++;
    dismiss();
    void api()?.cancel_window_close().catch(()=>{});
  }
  async function prepare(forceAsk) {
    const serial=++sequence;
    phase='checking';
    view=null;
    if(dialog?.open)updateCopy();
    try {
      if(!edit().ready){
        await window.__Desktop_SETTINGS_BRIDGE__?.bootstrap?.();
        if(serial!==sequence)return;
      }
      const value=await api().prepare_window_close(edit());
      if(serial!==sequence)return;
      view=value;
      phase='ready';
      if(!forceAsk&&['EXIT','HIDE'].includes(value.automatic_action))await choose(value.automatic_action);
      else show();
    } catch(_){if(serial===sequence){
      phase='error';
      show();
    }}
  }
  function requestClose(forceAsk=false) {
    if(phase!=='idle'){
      if(dialog?.open)dialog.querySelector('[data-window-action=cancel]').focus();
      return true;
    }
    returnFocus=document.activeElement;
    void prepare(forceAsk);
    return true;
  }
  async function choose(choice) {
    if(phase!=='ready'||!view?.ticket)return;
    const current=edit();
    if(!current.ready||current.saving){
      view.edit=current;
      updateCopy();
      show();
      return;
    }
    phase='committing';
    updateCopy();
    try {
      const result=await api().commit_window_close({ticket:view.ticket,choice,edit:current});
      if(['RECONFIRM','BUSY'].includes(result.status)){
        await prepare(true);
        return;
      }
      if(!['EXIT_SCHEDULED','HIDDEN_TO_TRAY'].includes(result.status))throw new Error('CLOSE_STATE');
      dismiss();
    } catch(_){
      phase='error';
      show();
    }
  }
  async function refreshNative() {
    try {
      nativeState=await api().get_window_state();
      document.documentElement.classList.toggle('desktop-native-chrome',nativeState.status==='READY');
      updateCopy();
      scheduleGeometry();
    }
    catch(_){document.documentElement.classList.remove('desktop-native-chrome');}
  }
  function scheduleGeometry() {
    if(geometryFrame)return;
    geometryFrame=requestAnimationFrame(()=>{
      geometryFrame=0;
      if(nativeState?.status!=='READY')return;
      const bar=[...document.querySelectorAll('.desktop-global-bar')].find(e=>e.getBoundingClientRect().height&& !e.closest('[hidden]'));
      const rect=e=>{
        const r=e.getBoundingClientRect();
        return [r.left,r.top,r.right,r.bottom];
      };
      const value={scale:window.devicePixelRatio,drag:[],exclude:[],maximize:[]};
      if(bar&&!dialog?.open){
        value.drag=[rect(bar)];
        value.exclude=[...bar.querySelectorAll('button')].map(rect);
        const max=bar.querySelector('[data-window-action=max]');
        if(max)value.maximize=[rect(max)];
      }
      void api().update_window_regions(value).then(v=>{if(nativeState?.maximized!==v.maximized){
        nativeState=v;
        updateCopy();
      }}).catch(()=>{});
    });
  }
  function boot() {
    for(const bar of document.querySelectorAll('.desktop-global-bar')){
      const group=node('div','desktop-window-controls');
      group.dataset.desktopNoTranslate='true';
      for(const key of ['min','max','close']){
        const b=button(key,'desktop-window-control');
        b.append(icon(key));
        b.addEventListener('click',()=>key==='close'?requestClose():void api()?.window_action(key==='min'?'MINIMIZE':'MAXIMIZE').then(()=>setTimeout(refreshNative,100)).catch(()=>{}));
        if(key==='max')b.addEventListener('pointerenter',()=>{if(nativeState?.status==='READY')void api()?.window_action('MAXIMIZE_HOVER').catch(()=>{});});
        group.append(b);
      }
      bar.querySelector('.desktop-global-right')?.append(group);
      bar.addEventListener('pointerdown',event=>{
        if(event.button!==0||event.target.closest('button,a,input,select')||dialog?.open||nativeState?.status!=='READY')return;
        if(event.detail>1){
          void api().window_action('MAXIMIZE').then(()=>setTimeout(refreshNative,100));
          return;
        }
        void api().window_action('DRAG');
      });
      bar.addEventListener('contextmenu',event=>{
        if(event.target.closest('button')||nativeState?.status!=='READY')return;
        event.preventDefault();
        void api().window_action('SYSTEM_MENU');
      });
      new ResizeObserver(scheduleGeometry).observe(bar);
    }
    new MutationObserver(()=>{
      updateCopy();
      scheduleGeometry();
    }).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
    for(const surface of document.querySelectorAll('[data-desktop-route]'))new MutationObserver(scheduleGeometry).observe(surface,{attributes:true,attributeFilter:['hidden']});
    window.addEventListener('resize',scheduleGeometry);
    window.addEventListener('desktop:settings-locale-change',()=>{
      updateCopy();
      scheduleGeometry();
    });
    window.addEventListener('pywebviewready',refreshNative,{once:true});
    if(api())void refreshNative();
    updateCopy();
  }
  window.__Desktop_WINDOW_CHROME__=Object.freeze({requestClose,cancel,refreshNative,scheduleGeometry,
    inspect:()=>({phase,open:Boolean(dialog?.open),native:nativeState,view})});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();

