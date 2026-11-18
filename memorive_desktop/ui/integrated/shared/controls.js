/* Shared desktop-layout controls. No business calls, permissions or persistence. */
(() => {
  if (Memorive.controls) return;
  const iconSelector='.memo-tree-chevron,.memo-model-chevron,.icon-button,.desktop-settings-stepper button,.desktop-tasks-stepper button,.desktop-settings-disclosure-icon,.desktop-settings-category-chevron,.desktop-settings-api-chevron,.desktop-settings-cli-add-chevron';
  const paths={back:'m15 6-6 6 6 6',right:'m9 6 6 6-6 6',down:'m6 9 6 6 6-6',up:'m6 15 6-6 6 6',close:'m6 6 12 12M6 18 18 6',plus:'M12 5v14M5 12h14',minus:'M5 12h14',refresh:'M20 6v5h-5M4 18v-5h5M18.4 9A7 7 0 0 0 6.2 6.2L4 8M5.6 15a7 7 0 0 0 12.2 2.8L20 16'};
  const keys={'‹':'back','<':'back','›':'right','>':'right','⌄':'down','⌃':'up','^':'up','×':'close','＋':'plus','+':'plus','－':'minus','−':'minus','⟳':'refresh','↻':'refresh'};
  paths.leaderboard='M5 21v-6M12 21V9M19 21V3';
  function createIcon(key){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),
      p=document.createElementNS(svg.namespaceURI,'path');
    svg.setAttribute('viewBox','0 0 24 24');
    svg.setAttribute('class','desktop-symbol');
    svg.setAttribute('aria-hidden','true');
    p.setAttribute('d',paths[key]);
    svg.append(p);
    return svg;
  }
  function icons(root){
    const nodes=root instanceof Element?[root,...root.querySelectorAll(iconSelector)]:[...document.querySelectorAll(iconSelector)];
    for(const n of nodes){
      if(!n.matches(iconSelector)||n.classList.contains('desktop-toolbar-icon'))continue;
      const key=keys[n.textContent.trim()];
      if(!key)continue;
      const svg=createIcon(key);
      if(n.tagName==='BUTTON'){
        if(!n.hasAttribute('aria-label'))n.setAttribute('aria-label',n.dataset.desktopTip||({plus:'增加',minus:'减少',close:'关闭',back:'返回',refresh:'刷新'}[key])||'展开');
        n.classList.add('desktop-symbol-control');
      }
      n.replaceChildren(svg);
    }
    const scope=root instanceof Element?root:document;
    for(const n of [...scope.querySelectorAll('.desktop-settings-node-test-score,.desktop-tasks-node-test-score'),...(root?.matches?.('.desktop-settings-node-test-score,.desktop-tasks-node-test-score')?[root]:[])]){
      n.dataset.noScore=String(!/^\d/.test(n.textContent.trim()));
    }
    for(const n of scope.querySelectorAll('.desktop-tasks-task strong,.desktop-messages-copy strong'))n.title=n.textContent.trim();
  }
  const panelSelector='[id="desktop-right-panel"],[id="desktop-bottom-panel"],#settings-primary,#settings-node-settings';
  const focusOwners=new WeakMap();
  const panelStates=new WeakMap();
  function panel(n){
    if(!n.matches?.(panelSelector))return;
    const closed=n.hidden||n.getAttribute('aria-hidden')==='true';
    const wasClosed=panelStates.get(n);
    panelStates.set(n,closed);
    if(closed&&wasClosed===false&&n.id==='desktop-right-panel'){
      const root=n.closest('.desktop-page-root');
      // Route navigation and batch checkboxes are independent of detail selection.
      const views={
        'desktop-messages-preview':Memorive.messagesView,
        'desktop-library-preview':Memorive.libraryView,
        'desktop-worklog-preview':Memorive.workLogView
      };
      views[root?.id]?.closeDetail?.(false);
      if(root?.id==='desktop-inbox-preview'){
        root.querySelectorAll('.desktop-inbox-card[aria-selected="true"]').forEach(card=>card.setAttribute('aria-selected','false'));
      }
      root?.dispatchEvent(new CustomEvent('desktop:inspector-closed'));
    }
    if(closed&&n.contains(document.activeElement)){
      const root=n.closest('.desktop-page-root'),saved=focusOwners.get(n);
      const fallback=document.getElementById(n.id==='desktop-bottom-panel'?'desktop-toggle-bottom':'desktop-toggle-right');
      (saved?.isConnected&&!saved.closest('[hidden],[inert]')?saved:fallback)?.focus({preventScroll:true});
    }
    if(!closed&&n.inert&&!n.contains(document.activeElement))focusOwners.set(n,document.activeElement);
    n.inert=closed;
  }
  const busy=async(button,work)=>{
    if(!button||button.getAttribute('aria-busy')==='true')return;
    const disabled=button.disabled;
    button.disabled=true;
    button.setAttribute('aria-busy','true');
    try{return await work();}finally{
      button.disabled=disabled;
      button.removeAttribute('aria-busy');
    }
  };
  // SHARED: confirmation expires without executing. Only another explicit
  // click on the same action and same task can return true.
  let pendingRestart=null;
  function cancelRestart(){
    if(!pendingRestart)return;
    const row=pendingRestart;
    pendingRestart=null;
    clearInterval(row.timer);
    if(row.iconHtml!=null)row.button.innerHTML=row.iconHtml;else row.button.textContent=row.label;
    row.button.removeAttribute('data-restart-armed');
    row.button.removeAttribute('aria-pressed');
    delete row.button.dataset.desktopNoTranslate;
  }
  function confirmRestart(button,key,confirmationLabel=null){
    if(!button||button.disabled)return false;
    if(pendingRestart?.button===button && pendingRestart.key===key && performance.now()<pendingRestart.deadline){
      cancelRestart();
      return true;
    }
    cancelRestart();
    const row={button,key,label:button.textContent,iconHtml:button.dataset.iconConfirm==='true'?button.innerHTML:null,confirmationLabel,deadline:performance.now()+5000,timer:null};
    pendingRestart=row;
    button.dataset.restartArmed='true';
    button.dataset.desktopNoTranslate='true';
    button.setAttribute('aria-pressed','true');
    const tick=()=>{
      if(!button.isConnected || performance.now()>=row.deadline){
        cancelRestart();
        return;
      }
      const seconds=Math.ceil((row.deadline-performance.now())/1000);
      const language=document.documentElement.lang||'zh-CN';
      const label=row.confirmationLabel?(Memorive.app?.translate?.(row.confirmationLabel)||row.confirmationLabel):(language.startsWith('en')?'Click again':language.startsWith('ja')?'もう一度クリック':'再次点击确认');
      button.textContent=label+' · '+seconds+'s';
    };
    row.timer=setInterval(tick,100);
    tick();
    return false;
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape')cancelRestart();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cancelRestart();});
  document.addEventListener('click',e=>{
    if(pendingRestart&&!pendingRestart.button.contains(e.target))cancelRestart();
  },true);
  Memorive.controls=Object.freeze({icons,busy,createIcon,confirmRestart,cancelRestart,confirmAction:confirmRestart,cancelAction:cancelRestart});
  function init(){
    Memorive.settingsIcons?.apply();
    icons(document);
    document.querySelectorAll(panelSelector).forEach(panel);
    new MutationObserver(records=>{
      for(const r of records){
        if(r.type==='attributes'){
          panel(r.target);
          continue;
        }
        if(r.type==='characterData')icons(r.target.parentElement);
        for(const n of r.addedNodes)if(n instanceof Element){
          icons(n);
          panel(n);
          n.querySelectorAll(panelSelector).forEach(panel);
        }
      }
    }).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-hidden','hidden']});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();

