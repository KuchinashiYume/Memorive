(() => {
  'use strict';
  if(window.__Desktop_SETTINGS_CONTROLS__)return;
  const t=s=>window.__Desktop_INTEGRATED_APP__?.translate?.(s)||s;
  let active=null;
  function help(host,label,copy){
    const button=document.createElement('button');
    button.type='button';button.className='desktop-setting-help';button.setAttribute('aria-expanded','false');
    button.setAttribute('aria-label',t(label));button.dataset.helpCopy=copy;
    button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.4 9a2.7 2.7 0 0 1 5.2 1c0 2-2.6 2.2-2.6 3.8"/><path d="M12 17h.01"/></svg>';
    let pinned=false;
    const close=()=>{if(active?.button!==button)return;active.tip.remove();active=null;pinned=false;button.setAttribute('aria-expanded','false');};
    const show=()=>{
      if(active?.button===button)return;
      active?.close();
      const tip=document.createElement('div');tip.className='desktop-setting-help-popover';tip.setAttribute('role','tooltip');
      tip.id='desktop-help-'+crypto.randomUUID();tip.textContent=t(copy);
      tip.dataset.desktopNoTranslate='true';document.body.append(tip);
      button.setAttribute('aria-describedby',tip.id);button.setAttribute('aria-expanded','true');
      const bounds=button.getBoundingClientRect(),size=tip.getBoundingClientRect();
      tip.style.left=Math.max(8,Math.min(bounds.right-size.width,innerWidth-size.width-8))+'px';
      tip.style.top=(bounds.bottom+size.height+16<innerHeight?bounds.bottom+8:Math.max(8,bounds.top-size.height-8))+'px';
      active={button,tip,close};
    };
    button.addEventListener('pointerenter',show);button.addEventListener('focus',show);
    button.addEventListener('pointerleave',()=>{if(!pinned)close();});
    button.addEventListener('blur',()=>{if(!pinned)close();});
    button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(pinned)close();else{show();pinned=true;}});
    host.append(button);return button;
  }
  document.addEventListener('pointerdown',event=>{if(active&&!active.button.contains(event.target)&&!active.tip.contains(event.target))active.close();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')active?.close();});
  document.addEventListener('scroll',()=>active?.close(),true);
  function pendingRemoval(host,execute,changed=()=>{}){
    if(host.__pendingRemoval)return;
    const wasOpen=host.open;host.open=true;
    const row=document.createElement('div');row.className='desktop-pending-removal';row.setAttribute('role','status');
    const copy=document.createElement('span'),seconds=document.createElement('span');
    copy.textContent=t('即将移除');const now=document.createElement('button'),cancel=document.createElement('button');
    for(const b of [now,cancel]){b.type='button';b.className='soft-button';}
    now.textContent=t('立即移除');cancel.textContent=t('取消');
    row.append(copy,seconds,now,cancel);host.append(row);
    const deadline=Date.now()+5000;
    const finish=run=>{clearInterval(ticker);clearTimeout(timer);row.remove();host.__pendingRemoval=null;if(!run)host.open=wasOpen;if(run&&host.isConnected)execute();changed();};
    const tick=()=>{if(!host.isConnected){finish(false);return;}seconds.textContent=Math.max(0,Math.ceil((deadline-Date.now())/1000))+' s';};
    const ticker=setInterval(tick,100),timer=setTimeout(()=>finish(true),5000);
    host.__pendingRemoval={cancel:()=>finish(false),execute:()=>finish(true)};
    for(const [b,run] of [[now,true],[cancel,false]])b.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();finish(run);});
    tick();changed();
  }
  window.__Desktop_SETTINGS_CONTROLS__=Object.freeze({help,pendingRemoval});
})();

