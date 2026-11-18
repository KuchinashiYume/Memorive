/* The research Q&A page: projects and conversations, asking (memo.ask, then memo.job_status until the answer is in),
   answers and their versions, evidence and attachments, agents and desktop hand-off, and the chat's own settings.
   Calls the memo.* service methods. Publishes Memorive.chat (openThread, importFiles, pickFiles,
   configureThread, ...), used by the inbox home, the settings connections panel and app.py's self-test. */
(() => {
  'use strict';
  const q=s=>document.querySelector(s), t=s=>Memorive.app?.translate?.(s)||s;
  const create=(tag,text,cls)=>{
    const e=document.createElement(tag);
    if(text!=null)e.textContent=text;
    if(cls)e.className=cls;
    return e;
  };
  const call=(method,params={})=>Memorive.service.call(method,params);
  const errors={CLI_WORKSPACE_PATH_TOO_LONG:'当前目录过长，请将 Memo 放入较短的目录后重试',RULE_METRIC_INVALID:'请检查指标数值、年份与来源',RULE_SOURCE_REQUIRED:'请填写指标来源',VAULT_REQUIRED:'请先选择资料文件夹',CHAT_MODEL_UNAVAILABLE:'所选模型不可用，请检查模型设置',THREAD_BUSY:'当前对话正在回答',REVISION_CONFLICT:'内容已变化，请刷新后重试',EVIDENCE_STALE:'来源已修改，请更新索引',EVIDENCE_NOT_FOUND:'引用已失效，请更新索引',CHAT_MODEL_EXECUTION_FAILED:'模型未能完成回答，请查看调用记录',TENSION_NEEDS_TWO_SOURCES:'请选择至少两份文献证据',TENSION_NEEDS_TWO_PAPERS:'请选择来自不同文献的证据'};
  let state=null,
    project='default',
    thread=null,
    activeJob=null,
    pollTimer=0,
    selected=new Set(),
    lastEvidence=[],
    loaded=false,
    settingsProject=null,
    navIndex=null,
    searchText='',
    showArchived=false,
    refreshEpoch=0,
    restoreSelection=false,
    selectionEpoch=0,
    pendingSelectionEpoch=-1;
  Object.assign(errors,{ATTACHMENT_BUSY:'正在读取附件，请稍候再操作',ATTACHMENT_INTERRUPTED:'上次读取已中断，请点击重试',COMPARISON_CONTEXT_TOO_SMALL:'当前上下文容纳不下两篇资料，请在设置中增加上下文长度',FEEDBACK_EVIDENCE_REQUIRED:'请先添加资料并提问',MATERIAL_NOT_READY:'有附件尚未读取成功，请在资料栏处理后发送',MATERIAL_SELECTION_EMPTY:'请添加资料，或将提问范围切换为整个项目',ATTACHMENT_FORMAT_UNSUPPORTED:'暂不支持此格式，请使用 PDF、文本、Word 或图片',ATTACHMENT_EMPTY:'文件为空',ATTACHMENT_PASSWORD_REQUIRED:'文件已加密，请先解锁后添加',ATTACHMENT_VISION_MODEL_REQUIRED:'请在输入框选择能读取图片的模型，然后重试',ATTACHMENT_VISION_UNAVAILABLE:'所选模型暂不能读取图片，请更换模型后重试',LOCAL_OCR_HTTP_400:'所选本地模型暂不能读取这张图片，请更换模型后重试',SOURCE_TOO_LARGE:'文件超过 64 MB，请拆分后添加',TENSION_NEEDS_TWO_PAPERS:'比较分析需要至少两篇不同文献，请添加另一篇后发送',THREAD_ARCHIVED:'请先恢复这条对话',MANUAL_REASON_REQUIRED:'请填写调整理由',PENALTY_REASON_REQUIRED:'请填写调整理由',AGENT_MODEL_UNAVAILABLE:'请在设置中选择可用的 Agent 模型',AGENT_RUNNER_UNAVAILABLE:'Agent 暂不可用，请检查连接设置',OPPORTUNITY_CITATION_INVALID:'Agent 返回的引用无法核对，请重试'});
  function status(selector,message,error=false,kind=''){
    const e=q(selector);
    if(e){
      e.textContent=t(errors[message]||message);
      e.dataset.error=String(error);
      e.dataset.statusKind=kind;
    }
  }
  function fail(e,selector='#memo-chat-status'){status(selector,errors[e?.message]||e?.message||'操作未完成',true);}
  function action(id,fn){
    q(id)?.addEventListener('click',async e=>{
      e.preventDefault();
      try{await fn(e);}catch(err){
        fail(err,id.startsWith('#memo-')&&q(id).closest('#memo-settings-page')?'#memo-settings-status':q(id).closest('#memo-evidence-panel')?'#memo-evidence-status':'#memo-chat-status');
      }
    });
  }
  function button(label,fn,cls='soft-button'){
    const e=create('button',label,cls);
    e.type='button';
    e.onclick=async()=>{
      e.disabled=true;
      try{await fn();}catch(err){
        const d=e.closest('dialog');
        if(d){
          let alert=d.querySelector('.memo-dialog-error');
          if(!alert){
            alert=create('p',null,'memo-status memo-dialog-error');
            alert.setAttribute('role','alert');
            d.querySelector('.memo-dialog-body').append(alert);
          }
          alert.textContent=t(errors[err?.message]||err?.message||'操作未完成');
          alert.dataset.error='true';
          alert.scrollIntoView({block:'nearest'});
        }else fail(err,e.closest('#memo-evidence-panel')?'#memo-evidence-status':'#memo-chat-status');
      }finally{e.disabled=false;}
    };
    return e;
  }
  function route(name){
    const research=name==='research-settings';
    if(research)name='settings';
    Memorive.app?.activate(name);
    if(research)q('#memo-settings-category')?.click();
    void Memorive.productBridge.dispatch('navigation.select',{route:name},'memo-workspace').catch(fail);
  }
  function dialog(title){
    const d=create('dialog',null,'memo-dialog'),
      head=create('header'),
      body=create('div',null,'memo-dialog-body'),
      footer=create('footer');
    head.append(create('h3',title),button('关闭',()=>d.close(),'icon-button'));
    d.append(head,body,footer);
    const host=document.querySelector('.desktop-route-surface:not([hidden]) [id$="-preview"] .desktop-central')||document.querySelector('.desktop-route-surface:not([hidden]) [id$="-preview"]')||document.body;
    host.append(d);
    const focus=document.activeElement;
    d.addEventListener('close',()=>{
      d.remove();
      focus?.focus();
    });
    d.showModal();
    return {d,body,footer};
  }
  function field(label,value='',multiline=true){
    const wrap=create('label',null,'field-label');
    wrap.append(create('span',label));
    const input=create(multiline?'textarea':'input',null,'field');
    input.value=value;
    wrap.append(input);
    return {wrap,input};
  }
  function option(select,value,label,disabled=false){
    const o=create('option',label);
    o.value=value;
    o.disabled=disabled;
    select.append(o);
  }
  Object.assign(errors,{"AGENT_NAME_REQUIRED": "请填写 Agent 名称", "AGENT_SAVE_FIRST": "请先保存 Agent 设置", "AGENT_DESKTOP_MODEL_REQUIRED": "请先填写桌面 Agent 模型", "AGENT_DESKTOP_NOT_FOUND": "未找到桌面应用，请选择应用文件", "AGENT_APP_PATH_INVALID": "应用路径无效，请重新选择 EXE", "AGENT_CONFIG_INVALID": "请检查 Agent 配置", "HANDOFF_CHANGED": "交接文件已变化，请重新准备", "HANDOFF_NOT_FOUND": "交接资料不存在，请重新准备", "HANDOFF_VERSION_CHANGED": "研究内容已变化，请重新准备交接", "HANDOFF_SOURCE_CHANGED": "交接来源已变化，请更新索引并重新准备", "HANDOFF_THREAD_CHANGED": "原对话已不可用，请从当前对话重新交接", "HANDOFF_LEGACY_RECREATE_REQUIRED": "请重新准备带有版本记录的交接", "HANDOFF_BINDING_MISMATCH": "交接记录不匹配，请重新准备", "FEEDBACK_EVIDENCE_REQUIRED": "请选择当前有效的来源", "FEEDBACK_REVISION_PARENT_CONFLICT": "修订不能依赖将被替换的旧知识，请选择当前原始来源", "EVIDENCE_NO_CURRENT_SOURCE": "所选知识的来源已失效，请选择当前有效的原始资料", "HANDOFF_NO_CURRENT_SOURCE": "暂无可交接的有效资料，请更新来源后重新准备"});
Object.assign(errors,{"ANSWER_PATH_CHANGED": "当前回答路径已变化，请确认后重新提交", "ANSWER_ANCESTOR_CHANGED": "之前的回答已修改，请在当前路径重新提问", "RETRY_CONTEXT_EXCEEDS_CONTEXT_BUDGET": "本次风格调整超出上下文容量，请重新提问", "RETRY_TOPIC_CONTEXT_CHANGED": "课题条件已变化，请在当前资料下重新提问", "RETRY_CONTEXT_UNAVAILABLE": "此历史回答没有完整生成记录，无法重试", "RETRY_MATERIAL_SCOPE_CHANGED": "资料范围已变化，请重新提问", "MEMORY_CHANGED_DURING_REQUEST": "使用的记忆已变化，本次结果未保存", "CHAT_MODEL_CONFIGURATION_CHANGED": "模型配置已变化，请确认后重新提交", "RETRY_SIDE_EFFECT_NOT_ALLOWED": "此操作不能通过回答重试再次执行", "RETRY_MODEL_REQUIRED": "资料摘录模式不支持生成重试"});
  const dirtyForm=new Map(),memoryChanges=new Map(),memoryExpanded=new Set();
  let settingsCreating=false,saveInFlight=false,modelInFlight=false,modelDraftTarget=null,agentLabels=[];
  const formIds=['memo-model','memo-memory-enabled','memo-interest-enabled','memo-context','memo-recent','memo-agent','memo-agent-model','memo-custom-agents','memo-desktop-agents','memo-project-name','memo-vault','memo-memory-text','memo-memory-scope'];
  const formKey=id=>['memo-project-name','memo-vault'].includes(id)?(settingsCreating?'::new-project':project)+':'+id:id;
  function markDirty(id){
    const el=q('#'+id);
    if(id==='memo-model')modelDraftTarget=thread?.id||null;
    dirtyForm.set(formKey(id),el.type==='checkbox'?el.checked:el.value);
    Memorive.settingsBridge?.syncSaveState();
  }
  const currentModel=()=>thread&&Object.hasOwn(thread,'profile_ref')?thread.profile_ref:state?.settings.profile_ref;
  function restoreDirty(){
    for(const id of formIds){
      const key=formKey(id),el=q('#'+id);
      if(dirtyForm.has(key)){if(el.type==='checkbox')el.checked=dirtyForm.get(key);else el.value=dirtyForm.get(key);}
    }
    syncModelPicker();
    renderAgentList(dirtyForm.get('memo-agent')||q('#memo-agent').value);
    renderAgentChoices(dirtyForm.get('memo-agent-model')||q('#memo-agent-model').value);
  }
  const drafts=new Map(),draftRows=new Map(),draftWrites=new Map(),readingPositions=new Map();
  let renderedKey=null;
  const draftKey=()=>thread?thread.id+'::'+(thread.branch_key||'root'):project+'::new';
  function persistDraft(owner,branch,key,text){
    drafts.set(key,text);
    if(!owner)return;
    const previous=draftWrites.get(key)||Promise.resolve();
    const next=previous.catch(()=>{}).then(async()=>{
      let row=draftRows.get(key)||await call('memo.answer_draft_get',{thread_id:owner,branch_key:branch});
      if(row.text!==text)row=await call('memo.answer_draft_save',{thread_id:owner,branch_key:branch,text,expected_revision:row.revision});
      draftRows.set(key,row);
    }).catch(()=>{if(draftKey()===key)status('#memo-chat-status',t('草稿保存失败，输入仍保留在当前窗口'),true);});
    draftWrites.set(key,next);
  }
  function saveDraft(){persistDraft(thread?.id,thread?.branch_key||'root',draftKey(),q('#memo-question').value);}
  function loadDraft(){
    const key=draftKey();
    q('#memo-question').value=drafts.get(key)||'';
    if(!thread||drafts.has(key))return;
    call('memo.answer_draft_get',{thread_id:thread.id,branch_key:thread.branch_key||'root'}).then(row=>{
      draftRows.set(key,row);
      if(!drafts.has(key)){
        drafts.set(key,row.text);
        if(draftKey()===key)q('#memo-question').value=row.text;
      }
    }).catch(()=>{});
  }
  function rememberReading(){if(renderedKey)readingPositions.set(renderedKey,q('#memo-transcript').scrollTop);}
  async function selectAnswer(answerId,focusAction='previous'){
    if(!thread)return;
    const owner=thread.id,epoch=++selectionEpoch;
    saveDraft();
    rememberReading();
    closeMenu();
    const current=await call('memo.thread_get',{thread_id:owner});
    if(epoch!==selectionEpoch||thread?.id!==owner)return;
    const fresh=await call('memo.answer_select',{thread_id:owner,answer_id:answerId,expected_revision:current.revision});
    if(epoch!==selectionEpoch||thread?.id!==owner)return;
    thread=fresh;
    loadDraft();
    renderThread();
    const target=[...q('#memo-transcript').querySelectorAll('[data-message-id]')].find(el=>el.dataset.messageId===answerId);
    target?.querySelector('[data-answer-action="'+focusAction+'"]')?.focus({preventScroll:true});
  }
  async function retryAnswer(message,styleId=null){
    if(activeJob||waitingForFiles||thread?.archived||!message.retry_available)return;
    const owner=thread.id,epoch=selectionEpoch;
    saveDraft();
    rememberReading();
    closeMenu();
    waitingForFiles=true;
    busy(true);
    try{
      const params={thread_id:owner,answer_id:message.id,request_id:crypto.randomUUID()};
      if(styleId!==null)params.style_id=styleId;
      const job=await call('memo.answer_retry',params);
      if(epoch!==selectionEpoch||thread?.id!==owner)return;
      activeJob=job.id;
      renderThread();
      await poll();
    }finally{
      waitingForFiles=false;
      if(!activeJob)busy(false);
      renderComposer();
    }
  }
  function answerActions(message){
    const box=create('div',null,'memo-answer-actions'),
      versions=message.answer_variants||[],
      index=(message.variant_number||1)-1;
    if(versions.length>1){for(const [symbol,delta,action,label] of [['‹',-1,'previous','上一个回答'],['›',1,'next','下一个回答']]){
      if(delta===1)box.append(create('span',(index+1)+' / '+versions.length,'memo-answer-counter'));
      const available=index+delta>=0&&index+delta<versions.length;
      const b=button(symbol,()=>available?selectAnswer(versions[index+delta].id,action):undefined,'memo-quiet-button');
      b.dataset.answerAction=action;
      b.setAttribute('aria-disabled',String(!available));
      b.setAttribute('aria-label',t(label));
      box.append(b);
    }}
    const retry=button('',()=>retryAnswer(message),'memo-quiet-button memo-message-icon');
    retry.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/></svg>';
    retry.setAttribute('aria-label',t('重试回答'));
    retry.dataset.desktopTip=t('重试回答');
    retry.dataset.answerAction='retry';
    const unavailable=!message.retry_available||!!activeJob||waitingForFiles||!!thread.archived;
    retry.disabled=unavailable;
    box.append(retry);
    return box;
  }
const collapsed=new Set();
  let menu=null,menuAnchor=null,searchTimer=0;
  const viewClient=crypto.randomUUID().replaceAll('-','');
  let viewSequence=0,visibleThread=null,viewReady=false,pageLoading=false;
  const chatIsActive=()=>Memorive.app?.inspect().active_route==='chat';
  async function viewReceipt(action,owner=null,opening=false){
    const receipt=await call('memo.thread_view',{client_id:viewClient+(opening?'_opening':''),sequence:++viewSequence,action,thread_id:owner});
    if(action==='heartbeat'&&receipt.nav_epoch!==undefined&&navIndex.nav_epoch!==undefined&&receipt.nav_epoch!==navIndex.nav_epoch)await refresh();
    return receipt;
  }
  function syncVisibleThread(){
    const owner=chatIsActive()?thread?.id||null:null;
    if(owner===visibleThread)return;
    visibleThread=owner;
    void viewReceipt(owner?'visible':'closed',owner).catch(()=>{visibleThread=null;});
  }
  async function recordDisplayed(owner,epoch){
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    if(epoch!==selectionEpoch||thread?.id!==owner||!chatIsActive()||document.hidden||q('#memo-transcript').dataset.threadId!==owner)return;
    visibleThread=owner;
    await viewReceipt('displayed',owner);
  }
  async function loadNextPage(){
    if(pageLoading||!navIndex?.next_cursor)return;
    const cursor=navIndex.next_cursor,epoch=refreshEpoch,query=searchText,archived=showArchived;
    pageLoading=true;
    try{
      const page=await call('memo.conversation_index',{query,archived,cursor});
      if(epoch!==refreshEpoch||query!==searchText||archived!==showArchived)return;
      const seen=new Set(navIndex.threads.map(r=>r.id));
      navIndex={...page,threads:navIndex.threads.concat(page.threads.filter(r=>!seen.has(r.id)))};
      renderTree();
    }finally{pageLoading=false;}
  }
  async function copyText(text){
    const focused=document.activeElement,input=document.createElement('textarea');
    input.value=text;
    input.style.position='fixed';
    input.style.opacity='0';
    input.style.pointerEvents='none';
    document.body.append(input);
    let copied=false;
    try{
      input.select();
      copied=document.execCommand('copy');
    }finally{
      input.remove();
      if(focused?.isConnected)focused.focus({preventScroll:true});
    }
    if(!copied)throw Error('复制未完成，请在导出窗口选择文本后按 Ctrl C');
    status('#memo-chat-status','已复制');
  }
  function closeMenu(restoreFocus=false){
    if(menu)Memorive.controls?.cancelAction();
    menu?.remove();
    menu=null;
    if(restoreFocus)menuAnchor?.focus();
    menuAnchor=null;
  }
  async function refresh(){
    const epoch=++refreshEpoch,currentProject=project,selection=selectionEpoch;
    const [fresh,nav]=await Promise.all([call('memo.state',{project:currentProject,view_thread_id:thread?.id||''}),call('memo.conversation_index',{query:searchText,archived:showArchived})]);
    if(epoch!==refreshEpoch||selection!==selectionEpoch)return;
    const feedbackStamp=value=>JSON.stringify((value?.feedback||[]).map(p=>[p.id,p.revision,p.state]));
    let repaint=!!thread&&feedbackStamp(state)!==feedbackStamp(fresh);
    const preservePages=navIndex?.query===nav.query&&navIndex.archived===nav.archived&&navIndex.nav_epoch===nav.nav_epoch&&navIndex.threads.length>50;
    if(preservePages){
      const seen=new Set(nav.threads.map(r=>r.id));
      nav.threads=nav.threads.concat(navIndex.threads.filter(r=>!seen.has(r.id)));
      nav.next_cursor=navIndex.next_cursor;
    }
    state=fresh;
    navIndex=nav;
    loaded=true;
    if(!viewReady){
      viewReady=true;
      void viewReceipt('ready').catch(()=>{viewReady=false;});
    }
    for(const id of ['#memo-project','#memo-settings-project']){
      const el=q(id);
      el.replaceChildren();
      for(const p of state.projects)option(el,p.id,p.name);
      el.value=project;
    }
    if(restoreSelection){
      restoreSelection=false;
      const recent=nav.threads.find(r=>r.project===project);
      if(recent){
        const restored=await call('memo.thread_get',{thread_id:recent.id});
        if(epoch!==refreshEpoch||selection!==selectionEpoch)return;
        thread=restored;
      }
    }
    const current=state.threads.find(r=>r.id===thread?.id);
    if(current&&current.revision>=thread.revision){
      repaint ||= current.revision!==thread.revision;
      thread=current;
    }
    renderTree();
    const model=state.model_options.find(m=>m.profile_ref===(thread&&Object.hasOwn(thread,'profile_ref')?thread.profile_ref:state.settings.profile_ref));
    q('#memo-model-label').textContent=model?(model.profile_kind==='LOCAL'?t('本地模型'):model.profile_kind==='CLI'?'CLI':t('外部 API'))+' · '+model.model_name:t('本地检索 · 无模型调用');
    const job=state.jobs.find(j=>j.thread_id===thread?.id&&['QUEUED','RUNNING'].includes(j.status));
    if(job&&activeJob!==job.id){
      clearTimeout(pollTimer);
      activeJob=job.id;
      busy(true);
      pollTimer=setTimeout(()=>poll().catch(fail),200);
    }
    renderSettings();
    restoreDirty();
    renderThreadHeader();
    renderComposer();
    Memorive.settingsBridge?.syncSaveState();
    if(repaint)renderThread();
  }
  function renderThreadHeader(){
    const name=state?.projects.find(p=>p.id===project)?.name||'研究项目';
    updateAgentTip();
    q('#memo-project-crumb').textContent=name;
    q('#memo-thread-title').textContent=thread?.title||t('新对话');
    q('.memo-chat-toolbar').dataset.overview=String(!thread);
    q('#memo-back-overview').disabled=!thread;
    q('#memo-thread-menu').disabled=false;
    if(thread)q('#memo-temporary').checked=!!thread.temporary;
    q('#memo-temporary').disabled=!!activeJob;
    q('#memo-composer').dataset.archived=String(!!thread?.archived);
    if(thread?.archived)status('#memo-chat-status','这条对话已归档。请在对话菜单中恢复后继续。',false,'archived');
    else if(q('#memo-chat-status')?.dataset.statusKind==='archived')status('#memo-chat-status','');
    q('#memo-send').disabled=!!activeJob||!!thread?.archived||pendingSelectionEpoch===selectionEpoch;
    q('#memo-question').disabled=q('#memo-send').disabled;
  }
  async function selectThread(row){
    const epoch=++selectionEpoch;
    saveDraft();
    closeMenu();
    clearTimeout(pollTimer);
    activeJob=null;
    pendingSelectionEpoch=epoch;
    busy(true);
    try{
      await viewReceipt('opening',row.id,true);
      if(epoch!==selectionEpoch)return;
      let fresh=await call('memo.thread_get',{thread_id:row.id});
      if(epoch!==selectionEpoch)return;
      if(searchText&&row.matching_answer_ids?.length){
        fresh=await call('memo.answer_select',{thread_id:row.id,answer_id:row.matching_answer_ids[0],expected_revision:fresh.revision});
        if(epoch!==selectionEpoch)return;
      }
      clearTimeout(pollTimer);
      activeJob=null;
      project=fresh.project;
      thread=fresh;
      selected.clear();
      loadDraft();
      q('#memo-local-banner').hidden=true;
      await refresh();
      if(epoch===selectionEpoch){
        renderThread();
        await recordDisplayed(row.id,epoch);
      }
    }finally{
      if(pendingSelectionEpoch===epoch){
        void viewReceipt('closed',null,true).catch(()=>{});
        pendingSelectionEpoch=-1;
        busy(!!activeJob);
        renderThreadHeader();
        renderComposer();
      }
    }
  }
  function leaveThread(){
    selectionEpoch++;
    saveDraft();
    closeMenu();
    clearTimeout(pollTimer);
    activeJob=null;
    thread=null;
    selected.clear();
    busy(false);
    loadDraft();
    q('#memo-local-banner').hidden=true;
    status('#memo-chat-status','');
    renderTree();
    renderThread();
    renderComposer();
  }
  async function newThread(target=project){
    const epoch=++selectionEpoch;
    saveDraft();
    const temporary=q('#memo-temporary').checked;
    clearTimeout(pollTimer);
    activeJob=null;
    busy(false);
    const fresh=await call('memo.thread_create',{project:target,temporary});
    if(epoch!==selectionEpoch)return;
    clearTimeout(pollTimer);
    activeJob=null;
    project=target;
    thread=fresh;
    showArchived=false;
    q('#memo-archive-toggle').setAttribute('aria-pressed','false');
    collapsed.delete(project);
    selected.clear();
    loadDraft();
    q('#memo-local-banner').hidden=true;
    await refresh();
    if(epoch===selectionEpoch){
      renderThread();
      q('#memo-question').focus();
      await recordDisplayed(fresh.id,epoch);
    }
  }
  function renderTree(){
    const tree=q('#memo-threads'),
      scrollTop=tree.scrollTop,
      focusRow=tree.contains(document.activeElement)?document.activeElement.closest('[data-thread-id]')?.dataset.threadId:null;
    tree.replaceChildren();
    for(const p of navIndex.projects){
      const rows=navIndex.threads.filter(r=>r.project===p.id);
      if((searchText||showArchived)&&!rows.length)continue;
      const group=create('section',null,'memo-project-group');
      group.dataset.projectId=p.id;
      const head=create('div',null,'memo-project-row'),
        toggle=button('',()=>{
          collapsed.has(p.id)?collapsed.delete(p.id):collapsed.add(p.id);
          renderTree();
        },'memo-project-toggle');
      toggle.title=p.name;
      toggle.setAttribute('aria-expanded',String(!collapsed.has(p.id)||!!searchText||showArchived));
      toggle.append(create('span',collapsed.has(p.id)&&!searchText&&!showArchived?'›':'⌄','memo-tree-chevron'),create('span',p.name,'memo-project-name'),create('small',String(rows.length),'memo-project-count'));
      head.append(toggle);
      const plus=button('＋',()=>newThread(p.id),'memo-icon-action');
      plus.title=t('在此项目新建对话');
      plus.setAttribute('aria-label',t('在此项目新建对话'));
      head.append(plus);
      const projectMore=button('···',()=>projectMenu(p,projectMore),'memo-icon-action');
      projectMore.setAttribute('aria-label',t('项目操作'));
      head.append(projectMore);
      group.append(head);
      if(!collapsed.has(p.id)||searchText||showArchived){
        const list=create('div',null,'memo-project-threads');
        for(const row of rows){
          const wrap=create('div',null,'memo-thread-row');
          wrap.dataset.threadId=row.id;
          wrap.setAttribute('aria-current',String(thread?.id===row.id));
          const b=button('',()=>selectThread(row),'memo-thread');
          b.append(create('span',(row.pinned?'· ':'')+row.title,'memo-thread-name'));
          const meta=(showArchived?p.name+' · ':'')+(row.excerpt||(row.temporary?t('临时对话'):(row.question_count??Number((row.message_count/2).toFixed(0)))+' '+t('轮对话')));
          b.append(create('small',meta));
          b.setAttribute('aria-current',String(thread?.id===row.id));
          b.title=row.title;
          const more=button('···',()=>threadMenu(row,more),'memo-icon-action memo-thread-more');
          more.setAttribute('aria-label',t('对话操作')+' '+row.title);
          wrap.append(b,more);
          wrap.oncontextmenu=e=>{
            e.preventDefault();
            threadMenu(row,wrap,{x:e.clientX,y:e.clientY});
          };
          wrap.onkeydown=e=>{if(e.key==='ContextMenu'||e.shiftKey&&e.key==='F10'){
            e.preventDefault();
            threadMenu(row,wrap);
          }};
          list.append(wrap);
        }
        if(!rows.length)list.append(button(showArchived?'暂无归档对话':'＋ 开始一个对话',()=>newThread(p.id),'memo-empty-project'));
        group.append(list);
      }
      tree.append(group);
    }
    if(showArchived&&!navIndex.threads.length)tree.append(create('p',t('暂无归档对话'),'memo-tree-empty'));
    if(searchText&&!navIndex.threads.length)tree.append(create('p','没有匹配的对话，试试其他关键词。','memo-tree-empty'));
    q('#memo-thread-count').textContent=navIndex.threads.length+(navIndex.next_cursor?'+':'')+' '+t(showArchived?'条归档对话':'条对话');
    if(focusRow)tree.querySelector('[data-thread-id="'+CSS.escape(focusRow)+'"] .memo-thread')?.focus({preventScroll:true});
    tree.scrollTop=scrollTop;
    if(!navIndex.threads.length&&navIndex.next_cursor)setTimeout(()=>{void loadNextPage().catch(fail);},100);
  }
  function openMenu(anchor,items,point){
    closeMenu();
    const host=anchor.closest('#memo-evidence-panel')||q('#memo-chat-page');
    menu=create('div',null,'memo-context-menu');
    menu.setAttribute('role','menu');
    menuAnchor=anchor;
    host.append(menu);
    for(const [label,fn,cls,confirmationKey,options] of items){
      const b=button(t(label),async()=>{
        closeMenu();
        await fn();
      },'memo-menu-item '+(cls||''));
      if(confirmationKey){
        const execute=b.onclick;
        b.onclick=e=>{if(Memorive.controls?.confirmAction(b,confirmationKey,'再次点击确认删除'))return execute(e);};
      }
      b.setAttribute('role',options?'menuitemcheckbox':'menuitem');
      if(options){
        b.setAttribute('aria-checked',String(options.checked));
        b.disabled=!!options.disabled;
      }
      menu.append(b);
    }
    const rect=anchor.getBoundingClientRect(),bounds=host.getBoundingClientRect();
    const width=menu.offsetWidth,height=menu.offsetHeight,sidebar=host.id==='memo-evidence-panel';
    const left=(point?.x??(sidebar?rect.right-width:rect.left))-bounds.left;
    let top=(point?.y??rect.bottom+6)-bounds.top;
    if(!point&&top+height>bounds.height-8)top=rect.top-bounds.top-height-6;
    menu.style.left=Math.max(8,Math.min(left,bounds.width-width-8))+'px';
    menu.style.top=Math.max(8,Math.min(top,bounds.height-height-8))+'px';
    menu.querySelector('button')?.focus();
    menu.onkeydown=e=>{
      const items=[...menu.querySelectorAll('button')],i=items.indexOf(document.activeElement);
      if(e.key==='Escape'){
        e.preventDefault();
        e.stopPropagation();
        closeMenu(true);
      }
      if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
        e.preventDefault();
        items[e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();
      }
    };
  }
  function projectMenu(p,anchor){
    openMenu(anchor,[['新建对话',()=>newThread(p.id)],['重命名项目',()=>editProject(p)],['项目资料',()=>openMaterials(p.id)]]);
  }
  function editProject(p=null){
    const d=dialog(p?'重命名项目':'新建项目'),f=field('项目名称',p?.name||'',false);
    d.body.append(f.wrap);
    d.footer.append(button('取消',()=>d.d.close()),button('保存',async()=>{
      const fresh=p?state.projects.find(r=>r.id===p.id):null;
      const row=await call('memo.project_save',{name:f.input.value,vault:fresh?.vault||null,project:fresh?.id||null,expected_revision:fresh?.revision||0});
      d.d.close();
      await switchProject(row.id);
    },'primary-button'));
    f.input.focus();
  }
  async function mutateThread(row,changes){
    const fresh=await call('memo.thread_get',{thread_id:row.id});
    const result=await call('memo.thread_update',{thread_id:row.id,expected_revision:fresh.revision,changes});
    if(thread?.id===row.id){
      thread=result;
      if(result.archived&&!showArchived){
        saveDraft();
        thread=null;
        loadDraft();
      }
    }
    await refresh();
    renderThread();
  }
  function temporaryItem(){
    return ['临时对话',async()=>{
      q('#memo-temporary').checked=!q('#memo-temporary').checked;
      if(thread)await newThread();
    },'',null,{checked:q('#memo-temporary').checked,disabled:!!activeJob}];
  }
  function threadMenu(row,anchor,point){openMenu(anchor,[
    ['重命名',()=>{
      const d=dialog('重命名对话'),f=field('对话标题',row.title,false);
      d.body.append(f.wrap);
      d.footer.append(button('取消',()=>d.d.close()),button('保存',async()=>{
        await mutateThread(row,{title:f.input.value});
        d.d.close();
      },'primary-button'));
      f.input.focus();
      f.input.select();
    }],
    [row.pinned?'取消置顶':'置顶',()=>mutateThread(row,{pinned:!row.pinned})],
    [row.archived?'恢复到项目':'归档',()=>mutateThread(row,{archived:!row.archived})],
    ['复制对话',async()=>{
      const fresh=await call('memo.thread_get',{thread_id:row.id});
      const result=await call('memo.thread_duplicate',{thread_id:row.id,expected_revision:fresh.revision});
      showArchived=false;
      await selectThread(result);
    }],
    ['导出对话文本',async()=>{
      const out=await call('memo.thread_export',{thread_id:row.id});
      const d=dialog('导出对话文本'),f=field('可复制的 Markdown',out.text);
      f.input.readOnly=true;
      f.input.style.minHeight='240px';
      d.body.append(f.wrap);
      d.footer.append(button('全选文本',()=>{
        f.input.focus();
        f.input.select();
      }),button('复制',async()=>{
        await copyText(out.text);
        status('#memo-chat-status','对话文本已复制');
        d.d.close();
      },'primary-button'));
    }],
    temporaryItem(),
    ['删除对话',()=>deleteThread(row),'memo-menu-danger','delete-thread:'+row.id]
  ],point);}
  async function deleteThread(row){
    const fresh=await call('memo.thread_get',{thread_id:row.id});
    await call('memo.thread_forget',{thread_id:row.id,expected_revision:fresh.revision});
    if(thread?.id===row.id){
      selectionEpoch++;
      for(const key of drafts.keys())if(key.startsWith(thread.id+'::')){
        drafts.delete(key);
        draftRows.delete(key);
        readingPositions.delete(key);
      }
      thread=null;
      loadDraft();
      activeJob=null;
      busy(false);
    }
    await refresh();
    renderThread();
  }

  function messageBody(value){
    const body=create('div',null,'memo-message-body'),text=String(value??'');
    let offset=0;
    // Limited emphasis only: model text never becomes HTML, a URL, or an event.
    for(const match of text.matchAll(/\*\*([^*\n]+)\*\*/g)){
      body.append(document.createTextNode(text.slice(offset,match.index)));
      const strong=document.createElement('strong');
      strong.textContent=match[1];
      body.append(strong);
      offset=match.index+match[0].length;
    }
    body.append(document.createTextNode(text.slice(offset)));
    return body;
  }
  function renderThread(){
    renderThreadHeader();
    const log=q('#memo-transcript'),
      key=draftKey(),
      same=renderedKey===key,
      oldTop=log.scrollTop,
      oldHeight=log.scrollHeight,
      nearBottom=oldHeight-oldTop-log.clientHeight<60;
    rememberReading();
    const focused=document.activeElement,
      focusMessage=focused?.closest('[data-message-id]')?.dataset.messageId,
      focusAction=focused?.dataset.answerAction;
    log.classList.toggle('memo-preserve-reading',Boolean(renderedKey));
    log.replaceChildren();
    log.dataset.threadId=thread?.id||'';
    queueMicrotask(syncVisibleThread);
    if(!thread){
      const content=q('#memo-empty-template').content.cloneNode(true);
      content.querySelector('.desktop-home-expression-button')?.addEventListener('click',()=>q('#memo-question').focus());
      log.append(content);
    } else for(const m of thread.messages){
      const row=create('article',null,'memo-message');
      row.dataset.role=m.role;
      row.dataset.messageId=m.id;
      // The speaker's name is for screen readers; a question shows as a bubble, an answer as plain text.
      const head=create('header');
      head.append(create('strong',m.role==='user'?'你':'Memorive','memo-message-speaker'));
      const copy=create('button',null,'memo-quiet-button memo-copy-message memo-message-icon');
      copy.type='button';
      copy.onclick=()=>copyText(m.text).catch(err=>fail(err));
      copy.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>';
      copy.setAttribute('aria-label',t('复制'));
      copy.dataset.desktopTip=t('复制');
      copy.dataset.answerAction='copy';
      if(m.role==='user')head.append(copy);
      row.append(head,messageBody(m.text));
      if(m.feedback_id){
        const proposal=state?.feedback.find(p=>p.id===m.feedback_id);
        if(proposal){
          const details=create('details',null,'memo-knowledge-result'),
            summary=create('summary',t('可保存的结论'),'desktop-settings-disclosure');
          details.append(summary,create('h4',proposal.title),create('p',proposal.claim),create('p',t('适用范围')+' · '+proposal.scope),create('p',t('限制与待核实事项')+' · '+proposal.limitations));
          if(proposal.state==='review_pending')details.append(button('确认加入知识',()=>review(proposal),'soft-button'));
          else details.append(create('small',t(proposal.state==='active'?'已进入检索':'已处理')));
          row.append(details);
        }
      }
      renderOpportunities(row,m);
      if(m.citations?.length){
        const links=create('div',null,'memo-citations');
        m.citations.forEach((r,i)=>links.append(button('['+(i+1)+'] '+r.title,()=>showEvidence(r),'memo-citation')));
        row.append(links);
      }
      if(m.role==='assistant'){
        // Under an answer, one line: its actions first, then what it was made from and what it took.
        const foot=create('div',null,'memo-message-footer'),meta=create('span',null,'memo-message-meta');
        foot.append(answerActions(m),copy);
        if(m.needs_agent)foot.append(button('继续深入 → 交给 Agent',()=>handoff(),'memo-quiet-button'));
        if(m.engine)meta.append(create('span',m.engine==='evidence_only'?'资料摘录':m.engine));
        if(Number.isFinite(m.elapsed_ms)&&m.elapsed_ms>=0)meta.append(create('span',(m.elapsed_ms/1000).toFixed(1)+' s'));
        const usage=m.usage?.token_usage;
        if(usage)meta.append(create('span','Tokens · '+(usage.prompt_tokens??usage.input_tokens??'?')+' / '+(usage.completion_tokens??usage.output_tokens??'?')));
        const cached=usage?.cached_input_tokens??usage?.cache_read_input_tokens;
        if(cached>0)meta.append(create('span',t('缓存命中')+' · '+cached.toLocaleString()));
        if(m.usage&&Object.keys(m.usage).length)meta.append(create('span',t('费用')+' · '+(m.usage.actual_cost==null?t('未知'):String(m.usage.actual_cost))));
        if(m.context)row.append(contextDetails(m));
        if(meta.childElementCount)foot.append(meta);
        row.append(foot);
      }
      log.append(row);
    }
    lastEvidence=thread?.messages?.filter(m=>m.role==='assistant').at(-1)?.citations||[];
    renderEvidence();
    const savedTop=readingPositions.get(key),
      restoreTop=same&&nearBottom&&log.scrollHeight>oldHeight?log.scrollHeight:(savedTop??oldTop);
    // Restoring a branch is positional; CSS smooth scrolling would race the
    // next switch and capture an intermediate position as the saved location.
    log.scrollTo({top:restoreTop,behavior:'instant'});
    renderedKey=key;
    if(focusMessage&&focusAction){
      const row=[...log.querySelectorAll('[data-message-id]')].find(el=>el.dataset.messageId===focusMessage);
      row?.querySelector('[data-answer-action="'+focusAction+'"]')?.focus({preventScroll:true});
    }
  }
  function renderOpportunities(row,message){
    for(const item of message.opportunities||[]){
      const details=create('details',null,'memo-knowledge-result'),
        summary=create('summary',item.question,'desktop-settings-disclosure');
      details.append(summary,create('p',item.hypothesis),create('p',item.rationale),create('p',t('限制与待核实事项')+' · '+item.limitations));
      const refs=create('div',null,'memo-citations');
      (message.citations||[]).filter(r=>item.evidence_ids.includes(r.id)).forEach((r,i)=>refs.append(button(t('证据')+' '+(i+1),()=>showEvidence(r),'memo-citation')));
      details.append(refs);
      row.append(details);
    }
  }
  async function materialMetadata(target,a){
    const value=await call('memo.material_metadata',{project,artifact_id:a.id});
    if(!target.isConnected)return;
    const details=create('details',null,'desktop-settings-local-model-advanced memo-metadata'),
      summary=create('summary',t('重要性与记录'),'desktop-settings-disclosure');
    details.append(summary);
    const body=create('div',null,'memo-settings-disclosure-body'),
      score=field('重要性',value.manual_score??50,false),
      reason=field('调整理由',value.manual_reason),
      clear=button('清除手动重要性',async()=>{
        await call('memo.material_metadata_save',{project,artifact_id:a.id,expected_revision:value.revision,changes:{manual_score:null,manual_reason:''}});
        await previewMaterial(a);
      },'memo-quiet-button');
    score.input.type='range';
    score.input.min=0;
    score.input.max=100;
    score.input.className='form-range';
    const out=create('output',value.manual_score==null?t('未设置'):value.manual_score+'%');
    score.wrap.append(out);
    score.input.oninput=()=>out.textContent=score.input.value+'%';
    const info=create('details',null,'memo-saved-memory');
    info.append(create('summary',t('文献信息'),'desktop-settings-disclosure'));
    const bibliographic={...value.bibliography?.fields,...value.rule};
    const typeNames={'journal-article':'期刊论文','proceedings-article':'会议论文','book':'书籍','book-chapter':'书籍章节','dissertation':'学位论文','posted-content':'预印本','report':'报告','dataset':'数据集'};
    for(const [key,label] of [['year','年份'],['journal','期刊'],['cited_by','被引次数'],['doc_type','文献类型']])if(bibliographic[key]!=null)info.append(create('p',t(label)+' · '+(key==='doc_type'?t(typeNames[bibliographic[key]]||bibliographic[key]):bibliographic[key])));
    const jm=bibliographic.journal_metric;
    if(jm)info.append(create('p',(jm.kind==='jcr_jif'?'JCR JIF':'OpenAlex · '+t('两年平均被引'))+' · '+Number(jm.value).toLocaleString(undefined,{maximumFractionDigits:2})+(jm.year?' · '+jm.year:'')));
    for(const author of bibliographic.author_metrics?.authors||[])info.append(create('p',(author.name||author.id)+' · h-index '+author.h_index+' · '+(bibliographic.author_metrics.provider||'')));
    if(value.bibliography?.source)info.append(create('small',value.bibliography.source+(value.bibliography.enrichment?.status==='OBSERVED'?' / OpenAlex':'')+' · '+new Date(value.bibliography.observed_at).toLocaleDateString()));
    const metricEdit=create('div',null,'memo-settings-context-fields'),
      jif=field('JCR JIF',value.rule?.journal_metric?.kind==='jcr_jif'?value.rule.journal_metric.value:'',false),
      year=field('指标年份',value.rule?.journal_metric?.year||new Date().getFullYear()-1,false),
      source=field('指标来源',value.rule?.journal_metric?.source_id||'',false);
    jif.input.type='number';
    jif.input.min=0;
    jif.input.max=10000;
    jif.input.step='any';
    year.input.type='number';
    year.input.min=1900;
    year.input.max=new Date().getFullYear();
    metricEdit.append(jif.wrap,year.wrap);
    info.append(metricEdit,source.wrap,button('保存指标',async()=>{
      const rule={...value.rule};
      if(jif.input.value==='')delete rule.journal_metric;else {
        if(!source.input.value.trim())throw Error(t('请填写指标来源'));
        rule.journal_metric={kind:'jcr_jif',value:Number(jif.input.value),year:Number(year.input.value),source_id:source.input.value.trim()};
        rule.source=source.input.value.trim();
        rule.observed_at=new Date().toISOString();
      }
      await call('memo.material_metadata_save',{project,artifact_id:a.id,expected_revision:value.revision,changes:{rule}});
      await previewMaterial(a);
    },'soft-button'));
    body.append(info,score.wrap,reason.wrap);
    const actions=create('div',null,'memo-actions');
    actions.append(clear,button('应用',async()=>{
      await call('memo.material_metadata_save',{project,artifact_id:a.id,expected_revision:value.revision,changes:{manual_score:Number(score.input.value),manual_reason:reason.input.value}});
      await previewMaterial(a);
    },'soft-button'));
    body.append(actions);
    if(a.kind==='derived'){
      const penalty=field('权威性保留比例',Math.round((value.penalty_override??value.effective_penalty)*100),false),
        why=field('调整理由',value.penalty_reason);
      penalty.input.type='range';
      penalty.input.min=0;
      penalty.input.max=100;
      penalty.input.className='form-range';
      const out=create('output',penalty.input.value+'%');
      penalty.wrap.append(out);
      penalty.input.oninput=()=>out.textContent=penalty.input.value+'%';
      body.append(penalty.wrap,why.wrap,button('应用折扣调整',async()=>{
        await call('memo.material_metadata_save',{project,artifact_id:a.id,expected_revision:value.revision,changes:{penalty_override:Number(penalty.input.value)/100,penalty_reason:why.input.value}});
        await previewMaterial(a);
      }));
    }
    const records=create('details',null,'memo-saved-memory');
    records.append(create('summary',t('批注与引用记录'),'desktop-settings-disclosure'));
    for(const key of ['annotations','citations'])for(const item of value[key]){
      const line=create('div',null,'memo-source-row');
      line.append(create('span',(key==='citations'?({data:t('数据'),method:t('方法'),argument:t('论据')})[item.type]+' · ':'')+(item.text||item.id)),button('×',async()=>{
        await call('memo.material_metadata_save',{project,artifact_id:a.id,expected_revision:value.revision,changes:{[key]:value[key].filter(r=>r.id!==item.id)}});
        await previewMaterial(a);
      }));
      records.append(line);
    }
    const note=field('添加记录',''),kind=create('select',null,'select');
    for(const [key,title] of [['note','批注'],['data','数据引用'],['method','方法引用'],['argument','论据引用']])option(kind,key,title);
    records.append(note.wrap,kind,button('添加记录',async()=>{
      if(!note.input.value.trim())return;
      const key=kind.value==='note'?'annotations':'citations';
      await call('memo.material_metadata_save',{project,artifact_id:a.id,expected_revision:value.revision,changes:{[key]:[...value[key],{id:crypto.randomUUID(),text:note.input.value,type:kind.value}]}});
      await previewMaterial(a);
    },'memo-quiet-button'));
    body.append(records);
    if(value.history?.length){
      const history=create('details',null,'memo-saved-memory');
      history.append(create('summary',t('调整历史'),'desktop-settings-disclosure'));
      for(const item of [...value.history].reverse())history.append(create('p',new Date(item.changed_at).toLocaleString()+' · '+(item.manual_reason||item.penalty_reason||t('记录变更'))));
      body.append(history);
    }
    details.append(body);
    target.insertBefore(details,target.children[2]||null);
  }
  async function editFeedback(proposal){
    const target=q('#memo-feedback-list');
    target.replaceChildren();
    target.append(button('‹ '+t('返回知识'),()=>refresh(),'memo-quiet-button'));
    const editor=create('div',null,'memo-knowledge-editor');
    target.append(editor);
    const fields={};
    for(const [key,label] of [['title','标题'],['claim','可复用结论'],['scope','适用范围'],['limitations','限制与待核实事项']]){
      fields[key]=field(label,proposal[key],key!=='title');
      editor.append(fields[key].wrap);
    }
    const sources=create('fieldset'),choices=[],previous=new Set(proposal.parents.map(r=>r.id));
    sources.append(create('legend',t('核对本次修订的来源')));
    editor.append(sources);
    const candidates=new Map([...proposal.parents,...lastEvidence].map(r=>[r.id,r]));
    for(const ref of candidates.values()){
      let current;
      try{
        current=await call('memo.read_evidence',{evidence_id:ref.id,project,expected_hash:ref.content_hash});
      }catch(e){
        if(!['EVIDENCE_STALE','EVIDENCE_NOT_FOUND','EVIDENCE_INACTIVE','EVIDENCE_PROJECT_MISMATCH','EVIDENCE_VERSION_CONFLICT'].includes(e.message))throw e;
        continue;
      }
      if(current.artifact_id&&current.artifact_id===proposal.artifact_id)continue;
      const label=create('label',null,'memo-check'),check=create('input');
      check.type='checkbox';
      check.checked=previous.has(current.id);
      label.append(check,create('span',current.title+' · '+(current.page?'p. '+current.page:'L'+current.line_start)+' · '+current.text.slice(0,170)));
      sources.append(label);
      choices.push([check,current.id]);
    }
    sources.append(create('p',t(choices.length?'选择支持本次修订的当前来源；保存后仍需确认加入知识。':'暂无有效来源。请更新索引，使用更正后的资料提问，再返回修订并核对引用。')));
    const type=create('select',null,'select');
    type.setAttribute('aria-label',t('知识类型'));
    for(const [key,title] of [['synthesis','综合结论'],['concept','概念与方法'],['annotation','批注与补充']])option(type,key,title);
    type.value=proposal.derived_type||'synthesis';
    editor.append(type,button('保存待审版本',async()=>{
      const evidence_ids=choices.filter(([check])=>check.checked).map(([,id])=>id);
      if(!evidence_ids.length)throw Error('FEEDBACK_EVIDENCE_REQUIRED');
      const changes=Object.fromEntries(Object.entries(fields).map(([key,f])=>[key,f.input.value]));
      changes.derived_type=type.value;
      await call('memo.feedback_revise',{identity:proposal.id,expected_revision:proposal.revision,changes,evidence_ids});
      await refresh();
    },'primary-button'));
  }
  async function renderHealth(){
    const epoch=refreshEpoch,owner=project;
    const health=await call('memo.knowledge_health',{project:owner});
    if(epoch!==refreshEpoch||project!==owner)return;
    const list=q('#memo-feedback-list');
    list.querySelectorAll('.memo-health-observation').forEach(e=>e.remove());
    if(health.retrievals_observed){
      const summary=create('details',null,'memo-saved-memory memo-health-observation');
      summary.append(create('summary',t('检索观察'),'desktop-settings-disclosure'),create('p',t('替代原始资料的检索比例')+' · '+Math.round(health.parent_displacement_frequency*100)+'%'),create('p',t('已观察检索次数')+' · '+health.retrievals_observed));
      list.append(summary);
    }
    for(const item of health.items){
      const row=list.querySelector('[data-feedback-id="'+item.id+'"]');
      if(!row)continue;
      for(const issue of item.issues)row.prepend(create('small',t(issue==='source_changed'?'来源已变化，请重新核对':'已到复核时间'),'memo-inline-error'));
    }
  }
  function renderEvidence(){
    const target=q('#memo-evidence-list');
    target.replaceChildren();
    target.classList.toggle('memo-centered-empty',!lastEvidence.length);
    if(!lastEvidence.length)target.append(create('p',t('回答中的引用会显示在这里。'),'memo-empty-state'));
    for(const ref of lastEvidence){
      const card=create('article',null,'memo-evidence-card'),label=create('label'),check=create('input');
      check.type='checkbox';
      check.checked=selected.has(ref.id);
      check.onchange=()=>check.checked?selected.add(ref.id):selected.delete(ref.id);
      check.hidden=true;
      label.append(check,create('h4',ref.title));
      card.append(label,create('p',ref.text.slice(0,170)),create('small',(ref.page?'p. '+ref.page:'L'+ref.line_start+'–'+ref.line_end)+' · '+(ref.kind==='derived'?t('已确认知识'):t('原始资料'))),button('查看原文',()=>showEvidence(ref),'memo-quiet-button'));
      target.append(card);
    }
  }
  async function showEvidence(ref){
    const origin=materialView,owner=project;
    const fresh=await call('memo.read_evidence',{evidence_id:ref.id,project,expected_hash:ref.content_hash});
    if(project!==owner)return;
    showEvidencePanel('references');
    const list=q('#memo-evidence-list');
    list.replaceChildren(button('‹ '+t(origin==='feedback'?'返回知识':'返回引用'),()=>{
      showEvidencePanel(origin==='feedback'?'feedback':'references');
      renderEvidence();
    },'memo-quiet-button'),create('h4',fresh.title),create('small',fresh.page?t('页')+' '+fresh.page:'L'+fresh.line_start+'–'+fresh.line_end),create('pre',fresh.text),
      button('在外部查看',()=>window.pywebview.api.memo_open_evidence({evidence_id:ref.id,project,expected_hash:ref.content_hash}),'memo-quiet-button'));
  }
  function contextDetails(message){
    const details=create('details',null,'memo-saved-memory memo-context-details'),
      ids=new Set(message.context.memory_ids||[]);
    details.append(create('summary',t('使用的上下文'),'desktop-settings-disclosure'),create('p',t('记忆条数')+' · '+ids.size));
    for(const m of message.generation?.context.confirmed_memory||state.memories.filter(m=>ids.has(m.id))){
      const current=state.memories.find(row=>row.id===m.id);
      details.append(create('pre',current&&current.revision===m.revision?m.text:t('此记忆已变化或已遗忘')));
    }
    if(message.generation){
      const g=message.generation;
      details.append(create('p',t('回答风格')+' · '+t(g.style.label)),create('small',g.style.common_version+' / '+g.style.mode_version),create('p',t('实际指令')+' · '+g.actual_instruction_sha256));
    }
    details.append(create('p',t('摘要引用消息')+' · '+(message.context.summary_message_ids||[]).length),create('p',t('上下文字符数')+' · '+message.context.chars));
    Memorive.evidenceCheck?.bind(details,message,{threadId:thread.id,call,create,t});
    for(const warning of message.ranking_warnings||[])details.append(create('small',t('部分排序信息暂不可用')+' · '+warning.component,'memo-inline-error'));
    return details;
  }
  function busy(on){
    q('#memo-send').disabled=on||pendingSelectionEpoch===selectionEpoch;
    q('#memo-stop').hidden=!on;
    q('#memo-question').disabled=q('#memo-send').disabled;
  }
  async function poll(){
    if(!activeJob)return;
    const requested=activeJob,epoch=selectionEpoch,owner=thread?.id;
    const ownsView=()=>activeJob===requested&&selectionEpoch===epoch&&thread?.id===owner;
    const job=await call('memo.job_status',{job_id:requested});
    if(!ownsView())return;
    if(job.thread_id!==owner){
      activeJob=null;
      busy(false);
      renderComposer();
      return;
    }
    if(job.status==='QUEUED'||job.status==='RUNNING'){
      status('#memo-chat-status',job.cancel_requested?'正在停止，已发出的调用可能仍在完成':'正在检索与回答…');
      pollTimer=setTimeout(()=>poll().catch(fail),600);
      return;
    }
    try{
      if(job.status==='COMPLETE'){
        const fresh=await call('memo.thread_get',{thread_id:owner});
        if(!ownsView())return;
        saveDraft();
        thread=fresh;
        loadDraft();
        await refresh();
        if(ownsView()){
          renderThread();
          status('#memo-chat-status','回答已保存');
        }
      }else status('#memo-chat-status',job.error||job.status,true);
    }finally{if(ownsView()){
      activeJob=null;
      busy(false);
      renderComposer();
      renderThread();
    }}
  }
  async function send(e){
    e?.preventDefault();
    const question=q('#memo-question').value.trim();
    if(!question||activeJob||waitingForFiles||thread?.archived)return;
    const epoch=selectionEpoch;
    saveDraft();
    waitingForFiles=true;
    busy(true);
    status('#memo-chat-status','正在检索与回答…');
    try{
      if(!thread){
        const fresh=await call('memo.thread_create',{project,temporary:q('#memo-temporary').checked});
        if(epoch!==selectionEpoch)return;
        drafts.delete(project+'::new');
        thread=fresh;
      }
      const owner=thread.id;
      if(!await waitForFiles(owner)||epoch!==selectionEpoch)return;
      const sentKey=draftKey(),sentBranch=thread.branch_key||'root',sentText=q('#memo-question').value;
      persistDraft(owner,sentBranch,sentKey,sentText);
      const job=await call('memo.ask',{thread_id:owner,question,request_id:crypto.randomUUID(),path_token:thread.path_token});
      if(drafts.get(sentKey)===sentText){
        persistDraft(owner,sentBranch,sentKey,'');
        if(draftKey()===sentKey&&q('#memo-question').value===sentText)q('#memo-question').value='';
      }
      if(epoch!==selectionEpoch||thread?.id!==owner)return;
      activeJob=job.id;
      await refresh();
      await poll();
    }catch(err){if(epoch===selectionEpoch){
      busy(false);
      fail(err);
    }}finally{
      waitingForFiles=false;
      renderComposer();
    }
  }

  const customAgents=()=>JSON.parse(q('#memo-custom-agents').value||'[]');
  const desktopConfigs=()=>JSON.parse(q('#memo-desktop-agents').value||'{}');
  let newCustomAgentId=null;
  function isDesktopAgent(id,saved=false){
    const a=(saved?state?.settings.custom_agents||[]:customAgents()).find(a=>a.id===id);
    return ['codex-desktop','claude-desktop'].includes(id)||a?.kind==='desktop';
  }
  function agentName(id){
    return {codex:'Codex CLI','claude-code':'Claude Code CLI','codex-desktop':'Codex Desktop','claude-desktop':'Claude Code Desktop',other:t('其他 Agent')}[id]||customAgents().find(a=>a.id===id)?.name||'Agent';
  }
  function setAgentJSON(id,value){
    q('#'+id).value=JSON.stringify(value);
    markDirty(id);
  }
  function effectiveAgent(){
    const id=q('#memo-agent').value;
    return id==='other'&&customAgents().some(a=>a.id===newCustomAgentId)?newCustomAgentId:id;
  }
  function renderAgentList(selected){
    const select=q('#memo-agent');
    select.replaceChildren();
    for(const id of ['codex','claude-code','codex-desktop','claude-desktop','other'])option(select,id,agentName(id));
    for(const a of customAgents())if(a.id!==newCustomAgentId)option(select,a.id,a.name);
    select.value=selected||'codex';
  }
  function renderDesktopFields(){
    const selected=q('#memo-agent').value,isNew=selected==='other',editor=q('#memo-agent-editor');
    if(isNew&&!newCustomAgentId)newCustomAgentId='custom-'+crypto.randomUUID();
    const id=isNew?newCustomAgentId:selected,a=customAgents().find(a=>a.id===id),custom=isNew||!!a;
    const desktop=isNew?(a?.kind||'desktop')==='desktop':isDesktopAgent(id);
    const cli=q('#memo-agent-model').closest('.desktop-settings-model-setting'),
      fields=q('#memo-desktop-fields'),
      host=q(custom?'#memo-custom-models':'#memo-built-in-models');
    if(cli.parentElement!==host)host.append(cli,fields);
    q('#memo-built-in-models').hidden=custom;
    editor.hidden=!custom;
    q('#memo-agent-editor-title').textContent=t(isNew?'添加自定义 Agent':'编辑自定义 Agent');
    if(custom){
      q('#memo-custom-name').value=a?.name||'';
      q('#memo-custom-kind').value=a?.kind||'desktop';
    }
    q('#memo-remove-agent').hidden=!a||isNew;
    cli.hidden=desktop;
    fields.hidden=!desktop;
    const conf=desktopConfigs()[id]||{};
    q('#memo-desktop-model').value=conf.model||'';
    q('#memo-desktop-path').value=conf.path||'';
    q('#memo-custom-error').hidden=true;
  }
  function stageCustomAgent(){
    const selected=q('#memo-agent').value;
    if(selected!=='other'&&!customAgents().some(a=>a.id===selected))return;
    const id=selected==='other'?newCustomAgentId:selected,
      all=customAgents(),
      a={id,name:q('#memo-custom-name').value,kind:q('#memo-custom-kind').value};
    const index=all.findIndex(v=>v.id===id);
    if(index<0)all.push(a);else all[index]=a;
    setAgentJSON('memo-custom-agents',all);
    renderAgentList(selected);
    q('#memo-custom-error').hidden=true;
  }
  function customAgentsValid(){return customAgents().every(a=>a.name.trim()&&['desktop','cli'].includes(a.kind));}
  function recordDesktopFields(){
    stageCustomAgent();
    const configs=desktopConfigs(),id=effectiveAgent();
    configs[id]={model:q('#memo-desktop-model').value,path:q('#memo-desktop-path').value};
    setAgentJSON('memo-desktop-agents',configs);
  }
  function agentIcon(label,fn,kind){
    const b=button('',fn,'icon-button desktop-toolbar-icon desktop-toolbar-soft '+kind);
    b.setAttribute('aria-label',t(label));
    b.dataset.desktopTip=t(label);
    return b;
  }
  function fieldLabel(title,input){
    const label=create('label',null,'field-label');
    label.append(create('span',t(title)),input);
    return label;
  }
  async function desktopHandoff(question){
    if(hasSettingsDraft())throw Error('AGENT_SAVE_FIRST');
    if(!(state.settings.desktop_agents?.[state.settings.agent]?.model||'').trim())throw Error('AGENT_DESKTOP_MODEL_REQUIRED');
    await ensureThread();
    const owner=thread.id;
    if(!await waitForFiles(owner))return;
    q('#memo-handoff-inline')?.remove();
    const panel=create('section',null,'desktop-settings-api-card memo-inline-editor');
    panel.id='memo-handoff-inline';
    const head=create('header',null,'memo-inline-heading'),
      body=create('div',null,'memo-handoff-preview'),
      footer=create('footer',null,'memo-actions');
    head.append(create('strong',t('在桌面 Agent 中继续')),agentIcon('关闭',()=>panel.remove(),'desktop-action-cancel'));
    panel.append(head,body,footer);
    q('#memo-chat-status').after(panel);
    panel.scrollIntoView({block:'nearest'});
    const d={body,footer};
    const task=field('继续研究的问题',question);
    d.body.append(create('strong',agentName(state.settings.agent)),task.wrap,create('p',t('交接将核对当前可用历史与来源；停用内容不会加入。')));
    const report=create('p',null,'memo-status');
    report.setAttribute('role','status');
    d.body.append(report);
    let prepared=null;
    const copyPrepared=async()=>{
      await window.pywebview.api.memo_desktop_handoff_action({id:prepared.id,action:'validate'});
      await copyText(prepared.prompt);
    };
    const launch=async()=>{
      if(activeJob||waitingForFiles)throw Error('THREAD_BUSY');
      if(!prepared){
        report.textContent=t('正在准备交接资料…');
        prepared=await window.pywebview.api.memo_prepare_desktop_handoff({thread_id:owner,question:task.input.value});
        task.input.readOnly=true;
        const details=create('details'),summary=create('summary',t('交接内容'));
        details.append(summary,create('pre',prepared.prompt));
        if(prepared.warnings.length)details.append(create('p',t('部分资料不可用，请查看交接清单')));
        d.body.append(details);
        copy.hidden=false;
        folder.hidden=false;
      }
      await copyPrepared();
      report.textContent=t('提示词已复制');
      try{
        await window.pywebview.api.memo_desktop_handoff_action({id:prepared.id,action:'launch'});
        report.textContent=t('已请求打开应用，请粘贴提示词并发送');
        status('#memo-chat-status','交接资料已准备');
      }catch(e){
        report.textContent=t(errors[e.message]||e.message);
        report.dataset.error='true';
      }
    };
    const copy=agentIcon('复制提示词',copyPrepared,'desktop-action-copy'),
      folder=agentIcon('打开交接文件夹',()=>window.pywebview.api.memo_desktop_handoff_action({id:prepared.id,action:'folder'}),'desktop-action-folder-open');
    copy.hidden=true;
    folder.hidden=true;
    d.footer.append(copy,folder,agentIcon('打开应用并准备交接',launch,'desktop-action-external'));
  }
  async function handoff(){
    const question=q('#memo-question').value.trim()||thread?.messages.filter(m=>m.role==='user').at(-1)?.text;
    if(!question){
      status('#memo-chat-status','请输入要交给 Agent 的问题',true);
      q('#memo-question').focus();
      return;
    }
    if(activeJob||waitingForFiles)throw Error('THREAD_BUSY');
    if(isDesktopAgent(state.settings.agent,true))return desktopHandoff(question);
    await ensureThread();
    const owner=thread.id;
    if(!await waitForFiles(owner))return;
    const job=await call('memo.agent_dispatch',{thread_id:owner,question,request_id:crypto.randomUUID()});
    activeJob=job.id;
    q('#memo-question').value='';
    busy(true);
    status('#memo-chat-status','Agent 正在研究…');
    await refresh();
    await poll();
  }
  async function tension(){
    const ids=selected.size?[...selected]:lastEvidence.map(r=>r.id);
    const pack=await call('memo.prepare_tension',{project,evidence_ids:ids});
    const view=dialog('张力分析准备');
    view.body.append(create('p','先核对研究对象、方法、指标和边界条件，再判断结论是否存在真实矛盾。'),create('pre',pack.comparison_facets.join(' · ')));
    for(const r of pack.evidence)view.body.append(create('h4',r.title),create('pre',r.text));
    view.footer.append(button('交给 Agent 分析',()=>{
      q('#memo-question').value='比较这些研究的张力与可比性，引用证据，说明条件差异及局限。';
      view.d.close();
      return handoff();
    },'primary-button'));
  }
  async function propose(){
    const ids=selected.size?[...selected]:lastEvidence.map(r=>r.id);
    if(!ids.length)throw Error('FEEDBACK_EVIDENCE_REQUIRED');
    const d=dialog('提交知识回流草稿');
    const fields=[field('标题','',false),field('可复用结论'),field('适用范围'),field('限制与待核实事项')];
    fields.forEach(f=>d.body.append(f.wrap));
    d.body.append(create('p',t('所选证据数量')+' · '+ids.length));
    d.footer.append(button('保存草稿，稍后审核',async()=>{
      await call('memo.propose_feedback',{project,title:fields[0].input.value,claim:fields[1].input.value,scope:fields[2].input.value,limitations:fields[3].input.value,evidence_ids:ids});
      d.d.close();
      await refresh();
      route('chat');
      showEvidencePanel('feedback');
    },'primary-button'));
  }
  function renderAgentChoices(currentRef=''){
    const agent=effectiveAgent(),select=q('#memo-agent-model');
    renderDesktopFields();
    const known={codex:['codex_cli'],'claude-code':['claude_code','claude_code_cli']};
    const options=state.model_options.filter(r=>r.profile_kind==='CLI'&&(known[agent]?known[agent].includes(r.adapter_id):agent.startsWith('custom-')||!Object.values(known).flat().includes(r.adapter_id)));
    select.replaceChildren();
    option(select,'','使用常用 Agent 的可用模型');
    for(const m of options)option(select,m.profile_ref,m.display_name||m.model_name,!m.eligible);
    select.value=currentRef;
    Memorive.researchNavigation?.renderAgentOptions(options,select.value);
  }
  function renderSettings(){
    const s=state.settings,select=q('#memo-model');
    select.replaceChildren();
    option(select,'','本地检索 · 无模型调用');
    for(const kind of ['LOCAL','API','CLI'])for(const m of state.model_options.filter(r=>r.profile_kind===kind))option(select,m.profile_ref,(kind==='LOCAL'?t('本地'):kind)+' · '+m.model_name+(m.eligible?'':' · '+t('不可用')),!m.eligible);
    select.value=currentModel()||'';
    q('#memo-adjust-model').disabled=!!activeJob||waitingForFiles||!!thread?.archived||modelInFlight;
    select.disabled=q('#memo-adjust-model').disabled;
    q('#memo-memory-enabled').checked=s.memory_enabled;
    q('#memo-interest-enabled').checked=s.use_recent_interests;
    q('#memo-context').value=s.context_chars;
    q('#memo-recent').value=s.recent_messages;
    q('#memo-custom-agents').value=JSON.stringify(s.custom_agents||[]);
    q('#memo-desktop-agents').value=JSON.stringify(s.desktop_agents||{});
    renderAgentList(s.agent);
    renderAgentChoices(s.agent_profile_ref||'');
    settingsProject=settingsCreating?null:state.projects.find(p=>p.id===project);
    q('#memo-project-name').value=settingsProject?.name||'';
    q('#memo-vault').value=settingsProject?.vault||'';
    status('#memo-index-status',t('已索引资料')+' · '+state.artifacts.filter(a=>a.state==='active').length);
    const scope=q('#memo-memory-scope'),savedScope=scope.value;
    scope.replaceChildren();
    option(scope,'user','所有项目');
    for(const p of state.projects)option(scope,'project:'+p.id,p.name+' · '+t('项目记忆'));
    scope.value=dirtyForm.get('memo-memory-scope')||savedScope||'project:'+project;
    if(!scope.value)scope.value='project:'+project;
    renderMemories();
    syncModelPicker();
    renderHealth().catch(()=>{});
    const feedback=q('#memo-feedback-list');
    feedback.replaceChildren();
    if(thread?.messages?.some(m=>m.knowledge_draft_issue))feedback.append(create('p',t('部分知识草稿未通过核对，回答已保留。'),'memo-inline-error'));
    feedback.classList.toggle('memo-centered-empty',!state.feedback.length);
    if(!state.feedback.length)feedback.append(create('p',t('尚无待审核草稿'),'memo-empty-state'));
    for(const p of state.feedback){
      const box=create('article',null,'memo-review');
      box.dataset.feedbackId=p.id;
      box.append(create('h4',p.title),create('p',p.claim),create('p',t('适用范围')+' · '+p.scope),create('p',t('限制与待核实事项')+' · '+p.limitations));
      const refs=create('div',null,'memo-citations');
      p.parents.forEach((r,i)=>refs.append(button(t('证据')+' '+(i+1),()=>showEvidence({...r,content_hash:r.content_hash}),'memo-citation')));
      box.append(refs);
      const actions=create('div',null,'memo-actions');
      if(p.state==='review_pending'){
        actions.append(button('确认加入知识',()=>review(p),'primary-button'),button('拒绝',async()=>{
          await call('memo.feedback_review',{identity:p.id,expected_revision:p.revision,proposal_hash:p.proposal_hash,decision:'reject',confirmed:true});
          await refresh();
        }));
      } else {
        actions.append(create('small',({active:'已进入检索',retracted:'已撤回',rejected:'已拒绝'})[p.state]||p.state));
        if(p.state==='active')actions.append(button('撤回知识',async()=>{
          await call('memo.feedback_retract',{identity:p.id,expected_revision:p.revision,confirmed:true});
          await refresh();
        }));
      }
      if(!p.superseded_by)actions.append(button(p.state==='review_pending'?'编辑草稿':'修订或复核',()=>editFeedback(p),'memo-quiet-button'));
      if(p.state==='active'){
        const a=state.artifacts.find(a=>a.id===p.artifact_id);
        if(a)actions.append(button('重要性与记录',()=>previewMaterial(a),'memo-quiet-button'));
      }
      if(p.superseded_by)actions.append(create('small',t('已有更新版本')));
      box.append(actions);
      feedback.append(box);
    }
  }
  async function review(p){
    await call('memo.feedback_review',{identity:p.id,expected_revision:p.revision,proposal_hash:p.proposal_hash,decision:'accept',confirmed:true});
    await refresh();
    renderThread();
  }
  async function switchProject(value){
    const epoch=++selectionEpoch;
    saveDraft();
    settingsCreating=false;
    clearTimeout(pollTimer);
    activeJob=null;
    busy(false);
    project=value;
    thread=null;
    selected.clear();
    loadDraft();
    await refresh();
    if(epoch===selectionEpoch)renderThread();
  }
  async function index(){
    status('#memo-index-status','正在更新索引…');
    const r=await call('memo.index_refresh',{project});
    await refresh();
    status('#memo-index-status',t('已索引资料')+' · '+r.indexed+(r.failures.length?' · '+t('部分资料需要处理')+' '+r.failures.length:''));
  }
  async function libraryAdd(){
    const result=await call('memo.library_list'),d=dialog('从 Memo 资料库添加'),choices=[];
    d.body.append(create('p','选择当前研究问答项目需要检索的主资料库处理产物。原文件会保留；内容变化后需重新添加。'));
    if(!result.items.length)d.body.append(create('p','主资料库暂无可用处理产物，请先在收件箱完成文献处理。'));
    for(const r of result.items){
      const label=create('label',null,'memo-check'),input=create('input');
      input.type='checkbox';
      label.append(input,create('span',r.title+' · '+r.kind+' · '+r.state));
      d.body.append(label);
      choices.push([input,r]);
    }
    d.footer.append(button('添加所选资料',async()=>{
      const artifacts=choices.filter(([i])=>i.checked).map(([,r])=>({id:r.id,content_hash:r.content_hash}));
      const out=await call('memo.library_add',{project,artifacts});
      d.d.close();
      await refresh();
      status('#memo-index-status',t('已索引资料')+' · '+out.indexed+(out.failures.length?' · '+t('部分资料需要处理'):''));
    },'primary-button'));
  }
const preferenceIds=formIds.filter(id=>!['memo-project-name','memo-vault','memo-memory-text','memo-memory-scope'].includes(id));
  function hasSettingsDraft(){
    return preferenceIds.some(id=>dirtyForm.has(id))||!!q('#memo-memory-text').value.trim()||memoryChanges.size>0;
  }
  async function saveProject(){
    const savedKeys=['memo-project-name','memo-vault'].map(formKey);
    const r=await call('memo.project_save',{name:q('#memo-project-name').value,vault:q('#memo-vault').value||null,project:settingsProject?.id||null,expected_revision:settingsProject?.revision||0});
    savedKeys.forEach(key=>dirtyForm.delete(key));
    if(r.id===project)await refresh();else await switchProject(r.id);
    if(r.vault)await index();
    renderThread();
    return r;
  }
  async function saveSettings(){
    if(saveInFlight)return {status:'BUSY'};
    if(!customAgentsValid()){
      q('#memo-custom-error').textContent=t('请填写 Agent 名称');
      q('#memo-custom-error').hidden=false;
      throw Error(t('请填写 Agent 名称'));
    }
    saveInFlight=true;
    try{
    if(preferenceIds.some(id=>dirtyForm.has(id))){
      const config={memory_enabled:q('#memo-memory-enabled').checked,use_recent_interests:q('#memo-interest-enabled').checked,context_chars:Number(q('#memo-context').value),recent_messages:Number(q('#memo-recent').value),agent:effectiveAgent(),agent_profile_ref:isDesktopAgent(effectiveAgent())?null:q('#memo-agent-model').value||null,custom_agents:customAgents().map(a=>({...a,name:a.name.trim()})),desktop_agents:desktopConfigs()},
        params={config,expected_revision:state.settings.revision};
      if(dirtyForm.has('memo-model')){
        config.profile_ref=q('#memo-model').value||null;
        if(modelDraftTarget){
          const owner=await call('memo.thread_get',{thread_id:modelDraftTarget});
          params.thread_id=owner.id;
          params.thread_revision=owner.revision;
        }
      }
      await call('memo.settings_save',params);
      if(params.thread_id&&thread?.id===params.thread_id){
        const updated=await call('memo.thread_get',{thread_id:params.thread_id});
        if(thread?.id===updated.id)thread=updated;
      }
      modelDraftTarget=null;
      newCustomAgentId=null;
      preferenceIds.forEach(id=>dirtyForm.delete(id));
    }
    for(const [id,change] of [...memoryChanges]){
      if(change.remove)await call('memo.memory_forget',{memory_id:id,expected_revision:change.original.revision});
      else await call('memo.memory_save',{text:change.text,scope:change.original.scope,project:change.original.project,memory_id:id,expected_revision:change.original.revision});
      memoryChanges.delete(id);
    }
    const memoryText=q('#memo-memory-text').value.trim();
    if(memoryText){
      const target=q('#memo-memory-scope').value;
      await call('memo.memory_save',{text:memoryText,scope:target==='user'?'user':'project',project:target==='user'?project:target.slice(8)});
      q('#memo-memory-text').value='';
      dirtyForm.delete('memo-memory-text');
    }
    await refresh();
    status('#memo-settings-status','');
    return {status:'PASS'};
    }finally{
      saveInFlight=false;
      Memorive.settingsBridge?.syncSaveState();
    }
  }
  async function revertSettings(){
    modelDraftTarget=null;
    newCustomAgentId=null;
    q('#memo-agent-editor').open=false;
    preferenceIds.forEach(id=>dirtyForm.delete(id));
    ['memo-project-name','memo-vault'].forEach(id=>dirtyForm.delete(formKey(id)));
    memoryChanges.clear();
    dirtyForm.delete('memo-memory-text');
    dirtyForm.delete('memo-memory-scope');
    q('#memo-memory-text').value='';
    settingsCreating=false;
    await refresh();
    status('#memo-settings-status','已恢复已保存的研究设置');
    return {status:'PASS'};
  }

  function syncModelPicker(){
    if(!state)return;
    const options=[{profileRef:'',model:'本地检索 · 无模型调用',state:'green',note:''},...state.model_options.map(m=>({profileRef:m.profile_ref,model:(m.profile_kind==='LOCAL'?'['+t('本地')+'] ':'')+m.model_name,state:m.eligible?'green':'yellow',note:t(m.profile_kind==='LOCAL'?'本地':'API')+(m.provider?' · '+m.provider:''),disabled:!m.eligible}))];
    Memorive.researchNavigation?.renderModelOptions(options,q('#memo-model').value);
  }
  function renderMemories(){
    const list=q('#memo-memory-list');
    list.replaceChildren();
    for(const original of state.memories){
      const change=memoryChanges.get(original.id),text=change?.text??original.text;
      const row=create('details',null,'desktop-settings-local-model-advanced memo-saved-memory');
      row.dataset.memoryId=original.id;
      row.open=memoryExpanded.has(original.id);
      row.ontoggle=()=>row.open?memoryExpanded.add(original.id):memoryExpanded.delete(original.id);
      const summary=create('summary',null,'desktop-settings-disclosure'),copy=create('span',null,'switch-copy');
      const label=original.scope==='user'?t('所有项目'):(state.projects.find(p=>p.id===original.project)?.name||original.project);
      const excerpt=create('strong',text.length>70?text.slice(0,70)+'…':text);
      excerpt.setAttribute('data-desktop-no-translate','');
      copy.append(excerpt,create('small',label+(change?' · '+t(change.remove?'待遗忘':'待保存'):'')));
      const arrow=create('span','›','desktop-settings-disclosure-icon');
      arrow.setAttribute('aria-hidden','true');
      summary.append(copy,arrow);
      const body=create('div',null,'memo-memory-detail'),content=create('p',text);
      content.setAttribute('data-desktop-no-translate','');
      body.append(content);
      const actions=create('div',null,'memo-actions');
      actions.append(button('编辑',()=>{
        const d=dialog('编辑记忆'),f=field('记忆内容',text);
        d.body.append(f.wrap);
        d.footer.append(button('取消',()=>d.d.close()),button('应用修改',()=>{
          if(!f.input.value.trim())throw Error('请输入记忆内容');
          memoryChanges.set(original.id,{original,text:f.input.value.trim()});
          d.d.close();
          renderMemories();
          Memorive.settingsBridge?.syncSaveState();
        },'primary-button'));
      },'memo-quiet-button'),button(change?.remove?'取消遗忘':'遗忘',()=>{
        if(change?.remove)memoryChanges.delete(original.id);else memoryChanges.set(original.id,{original,remove:true});
        renderMemories();
        Memorive.settingsBridge?.syncSaveState();
      },'memo-quiet-button'));
      body.append(actions);
      row.append(summary,body);
      list.append(row);
    }
  }
  function updateAgentTip(){
    const name=agentName(state?.settings.agent);
    q('#memo-handoff').dataset.desktopTip=t('交给 Agent')+' · '+t(name);
  }
  async function refreshAgents(){
    const settings=await call('settings.get_state');
    agentLabels=[...new Set((settings.settings?.cli_services||[]).filter(s=>s.enabled&&(s.models||[]).length).map(s=>s.display_name||({codex_cli:'Codex',claude_code:'Claude Code',claude_code_cli:'Claude Code'}[s.adapter_id])||s.adapter_id))];
    updateAgentTip();
  }
  function openSearch(open){
    q('#memo-search-wrap').hidden=!open;
    q('#memo-search-toggle').setAttribute('aria-expanded',String(open));
    if(open){
      q('#memo-thread-search').focus();
      q('#memo-thread-search').select();
    }
    else{
      clearTimeout(searchTimer);
      q('#memo-thread-search').value='';
      searchText='';
      refresh().catch(fail);
      q('#memo-search-toggle').focus();
    }
  }
  function showEvidencePanel(tab='materials'){
    const layout=q('#memo-layout');
    materialView=tab;
    setEvidenceOpen(true);
    layout.dataset.evidence='true';
    if(innerWidth<=1180)layout.dataset.history='false';
    q('#memo-evidence-toggle').setAttribute('aria-expanded','true');
    q('#memo-history-toggle').setAttribute('aria-expanded',layout.dataset.history);
    for(const b of document.querySelectorAll('[data-memo-evidence-tab]')){
      b.setAttribute('aria-pressed',String(b.dataset.memoEvidenceTab===tab));
      b.setAttribute('aria-selected',String(b.dataset.memoEvidenceTab===tab));
    }
    q('#memo-material-panel').hidden=tab!=='materials';
    q('#memo-reference-panel').hidden=tab!=='references';
    q('#memo-feedback-panel').hidden=tab!=='feedback';
    q('#memo-evidence-resizer').setAttribute('aria-valuenow',String(Math.round(q('.memo-evidence').getBoundingClientRect().width)));
  }
  async function openMaterials(id=project){
    if(id!==project)await switchProject(id);
    showEvidencePanel('materials');
    renderMaterials();
  }
  function initWorkspaceControls(){
    for(const selector of ['#memo-search-toggle','#memo-history-toggle','#memo-evidence-toggle']){
      const control=q(selector);
      const sync=()=>control.setAttribute('aria-pressed',control.getAttribute('aria-expanded'));
      sync();
      new MutationObserver(sync).observe(control,{attributes:true,attributeFilter:['aria-expanded']});
    }
    action('#memo-search-toggle',()=>openSearch(q('#memo-search-wrap').hidden));
    q('#memo-thread-search').addEventListener('keydown',e=>{if(e.key==='Escape'){
      e.preventDefault();
      e.stopPropagation();
      openSearch(false);
    }});
    const evidenceTabs=[...document.querySelectorAll('[data-memo-evidence-tab]')];
    for(const [index,b] of evidenceTabs.entries()){
      b.addEventListener('click',()=>showEvidencePanel(b.dataset.memoEvidenceTab));
      b.addEventListener('keydown',e=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
        e.preventDefault();
        const next=e.key==='Home'?0:e.key==='End'?evidenceTabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+evidenceTabs.length)%evidenceTabs.length;
        evidenceTabs[next].focus();
        showEvidencePanel(evidenceTabs[next].dataset.memoEvidenceTab);
      });
    }
    q('#memo-project-materials-dialog').querySelectorAll('[data-memo-project-close],#memo-project-materials-close').forEach(b=>b.addEventListener('click',()=>q('#memo-project-materials-dialog').close()));
    const resizer=q('#memo-evidence-resizer'),layout=q('#memo-layout');
    let resizing=null;
    const resize=value=>{
      const width=Math.max(280,Math.min(520,value));
      layout.style.setProperty('--memo-evidence-width',width+'px');
      resizer.setAttribute('aria-valuenow',String(Math.round(width)));
      resizer.setAttribute('aria-valuetext',Math.round(width)+' 像素');
    };
    resizer.addEventListener('pointerdown',e=>{
      if(e.button!==0)return;
      e.preventDefault();
      const scale=layout.getBoundingClientRect().width/layout.offsetWidth;
      resizing={x:e.clientX,width:q('.memo-evidence').getBoundingClientRect().width/scale,scale};
      resizer.setPointerCapture(e.pointerId);
    });
    resizer.addEventListener('pointermove',e=>{if(resizing)resize(resizing.width+(resizing.x-e.clientX)/resizing.scale);});
    resizer.addEventListener('pointerup',()=>resizing=null);
    resizer.addEventListener('pointercancel',()=>resizing=null);
    resizer.addEventListener('keydown',e=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
      e.preventDefault();
      const width=parseFloat(getComputedStyle(layout).getPropertyValue('--memo-evidence-width'))||340;
      resize(e.key==='Home'?280:e.key==='End'?520:width+(e.key==='ArrowLeft'?16:-16));
    });
    refreshAgents().catch(()=>{});
  }

  function init(){
    if(!q('#memo-chat-page')||!q('#memo-settings-page'))return;
    for(const id of formIds){
      q('#'+id)?.addEventListener('input',()=>markDirty(id));
      q('#'+id)?.addEventListener('change',()=>markDirty(id));
    }
    action('#memo-library-add',libraryAdd);
    q('#memo-composer').addEventListener('submit',e=>send(e).catch(fail));
    q('#memo-question').addEventListener('input',saveDraft);
    
    q('#memo-question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){
      e.preventDefault();
      void send().catch(fail);
    }});
    q('#memo-project').onchange=e=>switchProject(e.target.value).catch(fail);
    q('#memo-settings-project').onchange=e=>switchProject(e.target.value).catch(fail);
    action('#memo-new-thread',()=>newThread());
    initWorkspaceControls();
    action('#memo-tree-new-project',()=>editProject());
    action('#memo-back-overview',leaveThread);
    action('#memo-thread-menu',()=>{
      if(thread)threadMenu(thread,q('#memo-thread-menu'));else openMenu(q('#memo-thread-menu'),[temporaryItem()]);
    });
    action('#memo-archive-toggle',async()=>{
      showArchived=!showArchived;
      if(showArchived)collapsed.clear();
      q('#memo-archive-toggle').setAttribute('aria-pressed',String(showArchived));
      await refresh();
    });
    action('#memo-resume-local',()=>{
      q('#memo-local-banner').hidden=true;
      q('#memo-question').focus();
    });
    q('#memo-threads').addEventListener('scroll',()=>{
      const el=q('#memo-threads');
      if(el.scrollHeight-el.scrollTop-el.clientHeight<100)void loadNextPage().catch(fail);
    });
    q('#memo-threads').addEventListener('keydown',e=>{if(['End','PageDown'].includes(e.key))void loadNextPage().catch(fail);});
    q('#memo-thread-search').addEventListener('input',()=>{
      clearTimeout(searchTimer);
      searchTimer=setTimeout(()=>{
        searchText=q('#memo-thread-search').value.trim();
        refresh().catch(fail);
      },180);
    });
    document.addEventListener('pointerdown',e=>{if(menu&&!menu.contains(e.target))closeMenu();});
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape'&&menu)closeMenu(true);
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'&&Memorive.app?.inspect().active_route==='chat'){
        e.preventDefault();
        openSearch(true);
      }
    });
    action('#memo-history-toggle',()=>{
      const layout=q('#memo-layout'),show=getComputedStyle(q('.memo-conversations')).display==='none';
      layout.dataset.history=String(show);
      if(show&&innerWidth<=1180){
        setEvidenceOpen(false);
        layout.dataset.evidence='false';
      }
      q('#memo-history-toggle').setAttribute('aria-expanded',String(show));
      q('#memo-evidence-toggle').setAttribute('aria-expanded',layout.dataset.evidence);
    });
    // The handle on the projects panel's right edge collapses it; the handle on the chat's left edge opens it again.
    for(const [handle,other] of [['#memo-history-collapse','#memo-history-expand'],['#memo-history-expand','#memo-history-collapse']]){
      action(handle,()=>{
        q('#memo-history-toggle').click();
        q(other).focus({preventScroll:true});
      });
    }
    action('#memo-evidence-toggle',()=>{
      const layout=q('#memo-layout');
      if(layout.dataset.evidence==='true'){
        setEvidenceOpen(false);
        layout.dataset.evidence='false';
        q('#memo-evidence-toggle').setAttribute('aria-expanded','false');
      }else showEvidencePanel(materialView);
    });
    action('#memo-back-chat',()=>route('chat'));
    action('#memo-configure-models',()=>{
      route('settings');
      q('.desktop-settings-category[data-category="api"]')?.click();
    });
    action('#memo-stop',()=>call('memo.cancel',{job_id:activeJob}));
    action('#memo-handoff',handoff);
    action('#memo-tension',tension);
    action('#memo-propose',propose);
    action('#memo-refresh-index',refreshMaterials);
    action('#memo-reindex',index);
    action('#memo-save-settings',saveSettings);
    q('#memo-custom-name').addEventListener('input',stageCustomAgent);
    q('#memo-custom-kind').addEventListener('change',()=>{
      stageCustomAgent();
      renderAgentChoices(q('#memo-agent-model').value);
    });
    q('#memo-custom-name').addEventListener('blur',()=>{
      const invalid=customAgents().some(a=>a.id===effectiveAgent()&&!a.name.trim());
      q('#memo-custom-error').textContent=invalid?t('请填写 Agent 名称'):'';
      q('#memo-custom-error').hidden=!invalid;
    });
    q('#memo-remove-agent').addEventListener('click',e=>{
      e.preventDefault();
      e.stopPropagation();
    });
    action('#memo-remove-agent',()=>{
      const id=q('#memo-agent').value;
      setAgentJSON('memo-custom-agents',customAgents().filter(a=>a.id!==id));
      const configs=desktopConfigs();
      delete configs[id];
      setAgentJSON('memo-desktop-agents',configs);
      renderAgentList('codex');
      markDirty('memo-agent');
      renderAgentChoices();
      markDirty('memo-agent-model');
    });
    q('#memo-desktop-model').addEventListener('input',recordDesktopFields);
    action('#memo-pick-agent',async()=>{
      const r=await window.pywebview.api.memo_pick_agent_app();
      if(r.path){
        q('#memo-desktop-path').value=r.path;
        recordDesktopFields();
      }
    });
    action('#memo-detect-agent',async()=>{
      const apps=await window.pywebview.api.memo_desktop_apps();
      if(apps[q('#memo-agent').value]){
        q('#memo-desktop-path').value='';
        recordDesktopFields();
        status('#memo-settings-status','已识别桌面应用');
      }else status('#memo-settings-status','未找到桌面应用，请选择应用文件',true);
    });
    action('#memo-pick-vault',async()=>{
      const r=await window.pywebview.api.memo_pick_vault();
      if(r.path){
        q('#memo-vault').value=r.path;
        markDirty('memo-vault');
      }
    });
    action('#memo-new-project',()=>{
      settingsCreating=true;
      settingsProject=null;
      q('#memo-project-name').value='';
      q('#memo-vault').value='';
      markDirty('memo-project-name');
      markDirty('memo-vault');
      q('#memo-project-name').focus();
    });
    action('#memo-save-project',saveProject);
    q('#memo-memory-scope').addEventListener('change',async()=>{
      const target=q('#memo-memory-scope').value;
      if(target.startsWith('project:')&&target.slice(8)!==project)await switchProject(target.slice(8));
    });
    async function loadAgentConfig(){
      const info=q('#memo-agent-info');
      if(!q('#memo-agent-config-details').open)return;
      const r=await window.pywebview.api.memo_agent_config();
      info.textContent=(q('#memo-agent').value==='codex'?r.codex_config_toml:JSON.stringify({mcpServers:r.mcpServers},null,2))+'\n\n'+t('插件与 Skill')+'\n'+r.obsidian_plugin+'\n'+r.zotero_plugin+'\n'+r.skills;
    }
    q('#memo-agent-config-details').addEventListener('toggle',()=>loadAgentConfig().catch(e=>fail(e,'#memo-settings-status')));
    q('#memo-agent').addEventListener('change',()=>{
      renderAgentChoices();
      q('#memo-agent-model').dispatchEvent(new Event('change',{bubbles:true}));
      loadAgentConfig().catch(e=>fail(e,'#memo-settings-status'));
    });
    // These inputs belong to the research save participant, not the model-service draft.
    for(const event of ['input','change'])q('#memo-settings-page').addEventListener(event,e=>e.stopPropagation());
    Memorive.settingsBridge?.registerSaveParticipant('research-workspace',{isDirty:hasSettingsDraft,save:saveSettings,discard:revertSettings,validate:()=>customAgentsValid()&&(!hasSettingsDraft()||(Number(q('#memo-context').value)>=8000&&Number(q('#memo-context').value)<=64000&&Number.isInteger(Number(q('#memo-recent').value))&&Number(q('#memo-recent').value)>=2&&Number(q('#memo-recent').value)<=24))});
    window.addEventListener('desktop:settings-category-changed',e=>{if(e.detail.category==='research-workspace'){
      refresh().catch(fail);
      refreshAgents().catch(()=>{});
    }});
    let lastRoute='';
    const loadRoute=()=>{
      const r=Memorive.app?.inspect().active_route;
      if(r!==lastRoute){
        lastRoute=r;
        if(r==='chat')refreshAgents().catch(()=>{});
        if(r==='chat'||r==='settings')refresh().then(()=>{if(r==='chat')renderThread();}).catch(fail);
      }
    };
    window.addEventListener('desktop-integrated-route',()=>{
      syncVisibleThread();
      loadRoute();
    });
    window.addEventListener('pagehide',()=>{void viewReceipt('closed').catch(()=>{});});
    setInterval(()=>{
      if(chatIsActive()&&thread){void viewReceipt('heartbeat',thread.id).catch(()=>{});}else{
        syncVisibleThread();
        if(chatIsActive())void refresh().catch(fail);
      }
    },20000);
    window.addEventListener('pywebviewready',()=>{
      lastRoute='';
      loadRoute();
    },{once:true});
    queueMicrotask(loadRoute);
    new MutationObserver(()=>{if(loaded)refresh().then(renderThread).catch(fail);}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
    initAttachmentUI();
    renderComposer();
    Memorive.chat={openThread:id=>selectThread({id}),reportImportFailure:message=>{
      showEvidencePanel('materials');
      fail(Error(message),'#memo-evidence-status');
    },importFiles,pickFiles,configureThread,renderComposer,refreshMaterials,refresh,renderThread,send,saveSettings,revertSettings,hasSettingsDraft,inspect:()=>({loaded,project,thread_id:thread?.id,active_job:activeJob,evidence_count:lastEvidence.length}),openMaterials,showEvidencePanel,openSearch,openSettings:()=>route('research-settings')};
  }

  const importing=new Map(), importFailures=new Map();
  let composerPicker=null,waitingForFiles=false,materialView='materials';
  const chatShell=()=>Memorive.frames.chat;
  const chatRoot=()=>q('#memo-chat-page')?.closest('.desktop-route-surface');
  const setEvidenceOpen=open=>{chatShell()?.setInspector(open,{targetRatio:.32});};
  function materialTitle(a){return a.title.replace(/\s*[·•]\s*(?:Card|Analysis|清洗文档|原始文档|Core_)[\s\S]*$/i,'').trim();}
  function materialGroups(items){
    const groups=new Map();
    for(const a of items){
      const document=a.document_id||a.source_binding?.document_id||a.id;
      const key=/^[a-f0-9]{64}$/i.test(a.source_content_hash||'')?'doc_'+a.source_content_hash.toLowerCase():(/^doc_[a-f0-9]{64}$/i.test(document)?document.toLowerCase():document);
      if(!groups.has(key))groups.set(key,{id:key,title:materialTitle(a),items:[]});
      groups.get(key).items.push(a);
    }
    return [...groups.values()];
  }
  function usesProject(){return !!(thread?.include_project??(thread?.scope==='project'));}
  function currentMaterials(){
    const ids=new Set(thread?.artifact_ids||[]),excluded=new Set(thread?.excluded_artifact_ids||[]);
    return (state?.artifacts||[]).filter(a=>!excluded.has(a.id)&&['active','preparing','error'].includes(a.state)&&a.kind!=='derived'&&(!a.temporary_thread||a.temporary_thread===thread?.id)&&(ids.has(a.id)||(usesProject()&&!a.temporary_thread&&a.state==='active')));
  }
  async function removeMaterials(ids){
    if(!thread)return;
    const owner=thread.id,fresh=await call('memo.thread_get',{thread_id:owner});
    const updated=await call('memo.material_remove',{thread_id:owner,expected_revision:fresh.revision,artifact_ids:ids});
    if(thread?.id!==owner)return;
    thread=updated;
    await refresh();
    renderComposer();
    q('#memo-material-detail').hidden=true;
    q('#memo-material-list').hidden=false;
  }
  function renderComposer(){
    if(!state)return;
    const ref=currentModel();
    const options=[{profileRef:'',model:t('仅检索'),state:'green',note:t('不调用模型')},...state.model_options.map(m=>({
      profileRef:m.profile_ref,model:m.display_name||m.model_name,state:m.eligible?'green':'yellow',note:m.profile_kind==='LOCAL'?t('本地模型'):m.profile_kind,disabled:!m.eligible}))];
    composerPicker?.render(options,ref||'');
    const ids=new Set(thread?.artifact_ids||[]),
      groups=materialGroups(state.artifacts.filter(a=>ids.has(a.id))),
      host=q('#memo-attachments');
    host.replaceChildren();
    for(const group of groups){
      const a=group.items[0],card=create('div',null,'memo-attachment');
      card.dataset.artifactId=a.id;
      card.dataset.documentId=group.id;
      card.dataset.state=a.state;
      const open=button(group.title,()=>previewMaterial(a),'memo-attachment-name');
      open.setAttribute('data-desktop-no-translate','');
      open.title=group.title;
      card.append(open);
      if(group.items.some(a=>a.state!=='active'))card.append(create('small',t(group.items.some(a=>a.state==='preparing')?'正在读取…':'读取失败')));
      const remove=button('×',()=>removeMaterials(group.items.map(a=>a.id)),'memo-quiet-button');
      remove.setAttribute('aria-label',t('移除附件')+' '+group.title);
      remove.disabled=!!activeJob||waitingForFiles;
      card.append(remove);
      host.append(card);
    }
    host.hidden=!groups.length;
    const count=materialGroups(currentMaterials()).length;
    q('#memo-composer-scope').textContent=count+' '+t('份资料')+(usesProject()?' · '+t('包含项目资料'):'');
    q('#memo-composer-scope').setAttribute('data-desktop-tip',t('查看本次使用的资料'));
    q('#memo-composer-scope').setAttribute('aria-label',t('查看本次使用的资料'));
    q('#memo-composer-model').disabled=!!activeJob||waitingForFiles||!!thread?.archived||modelInFlight||saveInFlight;
    q('#memo-add-file').disabled=!!activeJob||waitingForFiles||!!thread?.archived;
    renderMaterials();
    positionComposerMenu();
  }
  // The model menu opens above the input box, as a chat input's menus do: it never covers the text being written.
  // It lines up with the model button and scrolls when the space above the box is short.
  function positionComposerMenu(){
    const menu=q('#memo-composer-model-menu'),anchor=q('#memo-composer-model');
    if(!menu||menu.dataset.open!=='true')return;
    const r=anchor.getBoundingClientRect(),
      host=q('#memo-composer').getBoundingClientRect(),
      box=q('#memo-input-shell').getBoundingClientRect(),
      top=q('#memo-transcript').getBoundingClientRect().top,
      width=Math.min(320,window.innerWidth-24);
    menu.style.width=width+'px';
    menu.style.left=Math.max(12,Math.min(r.left,window.innerWidth-width-12))-host.left+'px';
    menu.style.bottom=host.bottom-box.top+6+'px';
    menu.style.maxHeight=Math.min(340,Math.max(96,box.top-top-12))+'px';
  }

  async function ensureThread(){
    if(!thread)await newThread(project);
    if(thread.archived)throw Error('THREAD_ARCHIVED');
    return thread;
  }
  async function configureThread(changes){
    const epoch=selectionEpoch;
    await ensureThread();
    if(epoch!==selectionEpoch)return;
    const owner=thread.id;
    const fresh=await call('memo.thread_get',{thread_id:owner});
    if(epoch!==selectionEpoch||thread?.id!==owner)return;
    const updated=await call('memo.thread_configure',{thread_id:owner,expected_revision:fresh.revision,changes});
    if(epoch!==selectionEpoch||thread?.id!==owner)return;
    thread=updated;
    await refresh();
    if(epoch===selectionEpoch&&thread?.id===owner)renderComposer();
  }
  async function selectConversationModel(ref){
    if(modelInFlight||saveInFlight)return;
    const owner=thread?.id;
    modelInFlight=true;
    renderComposer();
    try{
      const params={config:{profile_ref:ref},expected_revision:state.settings.revision};
      if(owner){
        const fresh=await call('memo.thread_get',{thread_id:owner});
        params.thread_id=owner;
        params.thread_revision=fresh.revision;
      }
      await call('memo.settings_save',params);
      dirtyForm.delete('memo-model');
      modelDraftTarget=null;
      if(owner&&thread?.id===owner){
        const updated=await call('memo.thread_get',{thread_id:owner});
        if(thread?.id===owner)thread=updated;
      }
      await refresh();
    }finally{
      modelInFlight=false;
      renderComposer();
      q('#memo-adjust-model').disabled=!!activeJob||waitingForFiles||!!thread?.archived;
    }
  }
  async function importFiles(paths){
    if(!paths?.length)return {status:'CANCELLED'};
    const epoch=selectionEpoch;
    await ensureThread();
    if(epoch!==selectionEpoch)return {status:'CANCELLED'};
    const owner=thread.id,ownedProject=project;
    const ownsView=()=>thread?.id===owner&&selectionEpoch===epoch;
    const job=await call('memo.attach_files',{thread_id:owner,paths});
    if(ownsView()){
      const fresh=await call('memo.thread_get',{thread_id:owner});
      if(ownsView()){
        thread=fresh;
        await refresh();
        if(ownsView())showEvidencePanel('materials');
      }
    }
    const promise=(async()=>{
      let current=job;
      while(['QUEUED','RUNNING'].includes(current.status)){
        await new Promise(resolve=>setTimeout(resolve,450));
        current=await call('memo.attachment_status',{job_id:job.id});
        if(thread?.id===owner){
          await refresh();
          renderComposer();
        }
      }
      const failures=current.items.filter(i=>i.status==='ERROR');
      importFailures.set(owner,failures);
      if(ownsView()){
        const fresh=await call('memo.thread_get',{thread_id:owner});
        if(ownsView()){
          thread=fresh;
          await refresh();
          if(ownsView())renderComposer();
        }
      }
      return current;
    })();
    importing.set(job.id,{owner,promise});
    promise.catch(error=>{if(thread?.id===owner)fail(error,'#memo-evidence-status');}).finally(()=>importing.delete(job.id));
    return job;
  }
  async function pickFiles(){
    const result=await window.pywebview.api.pick_research_files();
    if(result.paths?.length)return importFiles(result.paths);
  }
  async function waitForFiles(owner){
    const epoch=selectionEpoch;
    const requests=[...importing.values()].filter(v=>v.owner===owner);
    if(requests.length){
      status('#memo-chat-status','正在准备附件，完成后自动发送…');
      await Promise.all(requests.map(v=>v.promise));
    }
    if(thread?.id!==owner||epoch!==selectionEpoch)return false;
    const fresh=await call('memo.thread_get',{thread_id:owner});
    if(thread?.id!==owner||epoch!==selectionEpoch)return false;
    thread=fresh;
    await refresh();
    if(thread?.id!==owner||epoch!==selectionEpoch)return false;
    const bad=state.artifacts.filter(a=>thread.artifact_ids?.includes(a.id)&&a.state!=='active');
    if(bad.length){
      showEvidencePanel('materials');
      status('#memo-chat-status','');
      fail(Error('MATERIAL_NOT_READY'),'#memo-evidence-status');
      return false;
    }
    return true;
  }
  function renderMaterials(){
    if(!state)return;
    const list=q('#memo-material-list');
    if(!list)return;
    list.replaceChildren();
    const note=create('div',null,'memo-material-scope-note');
    note.append(create('small','此处资料仅用于研究问答。需要进入主资料库时，请在收件箱完成文献处理。'),button('前往收件箱处理',()=>route('inbox'),'memo-quiet-button'));
    const groups=materialGroups(currentMaterials());
    list.classList.toggle('memo-centered-empty',!groups.length);
    if(!groups.length){
      const empty=create('div',null,'memo-empty-state');
      empty.append(create('p',t('添加文件后，资料会显示在这里。')),note);
      list.append(empty);
    }else list.append(note);
    for(const group of groups){
      const a=group.items[0],card=create('article',null,'memo-material-row');
      card.dataset.artifactId=a.id;
      card.dataset.documentId=group.id;
      const line=create('div',null,'memo-material-line'),
        open=button(group.title,()=>previewMaterial(a),'memo-material-title');
      open.setAttribute('data-desktop-no-translate','');
      open.title=group.title;
      const remove=button('×',()=>removeMaterials(group.items.map(r=>r.id)),'icon-button memo-material-remove');
      remove.setAttribute('aria-label',t('移除资料')+' '+group.title);
      remove.setAttribute('data-desktop-tip',t('从当前对话移除'));
      remove.disabled=!!activeJob||!!thread?.archived;
      line.append(open,remove);
      card.append(line);
      const failed=group.items.find(a=>a.state==='error'),preparing=group.items.some(a=>a.state==='preparing');
      card.append(create('small',t(failed?'读取失败':preparing?'正在读取…':'已就绪')+(a.page_count?' · '+a.page_count+' '+t('页'):'')));
      if(failed){
        card.append(create('p',t(errors[failed.error]||'文件未能读取，请更换模型后重试。'),'memo-inline-error'),button('重试',()=>importFiles([failed.path]),'memo-quiet-button'));
      }
      list.append(card);
    }
    for(const error of importFailures.get(thread?.id)||[]){
      if(error.id)continue;
      const card=create('article',null,'memo-material-row');
      card.append(create('strong',error.name),create('p',t(errors[error.error]||'文件未能读取'),'memo-inline-error'));
      list.append(card);
    }
  }

  async function previewMaterial(a){
    showEvidencePanel('materials');
    const target=q('#memo-material-detail');
    target.replaceChildren();
    target.hidden=false;
    target.append(button('‹ '+t('返回资料'),()=>{
      target.hidden=true;
      q('#memo-material-list').hidden=false;
    },'memo-quiet-button'),create('h4',materialTitle(a)));
    q('#memo-material-list').hidden=true;
    if(a.state!=='active'){
      target.append(create('p',t(a.error?errors[a.error]||'文件未能读取':'正在读取…')));
      return;
    }
    const result=await call('memo.material_preview',{project,artifact_id:a.id});
    status('#memo-evidence-status','');
    for(const ref of result.evidence){target.append(create('small',ref.page?t('页')+' '+ref.page:t('原文摘录')),create('pre',ref.text));}
    await materialMetadata(target,a);
    if(result.evidence[0])target.append(button('在外部查看',()=>window.pywebview.api.memo_open_evidence({project,evidence_id:result.evidence[0].id,expected_hash:result.evidence[0].content_hash}),'memo-quiet-button'));
  }
  async function refreshMaterials(){
    const b=q('#memo-refresh-index');
    b.disabled=true;
    b.classList.add('refreshing');
    b.setAttribute('aria-busy','true');
    const start=performance.now();
    try{
      const p=state.projects.find(p=>p.id===project);
      if(p?.vault)await call('memo.index_refresh',{project});
      await refresh();
      status('#memo-evidence-status','');
    }catch(error){fail(error,'#memo-evidence-status');}
    finally{
      await new Promise(r=>setTimeout(r,Math.max(0,350-(performance.now()-start))));
      b.disabled=false;
      b.classList.remove('refreshing');
      b.setAttribute('aria-busy','false');
    }
  }
  async function libraryInline(source='library'){
    await ensureThread();
    const owner=thread.id,ownerProject=project;
    showEvidencePanel('materials');
    const target=q('#memo-material-detail');
    target.replaceChildren();
    target.hidden=false;
    q('#memo-material-list').hidden=true;
    target.append(button('‹ '+t('返回资料'),()=>{
      target.hidden=true;
      q('#memo-material-list').hidden=false;
      renderMaterials();
    },'memo-quiet-button'));
    if(source==='project'){
      const row=create('label',null,'switch-row memo-project-scope-switch'),
        copy=create('span',t('自动检索本项目资料'),'switch-copy'),
        toggle=create('input',null,'switch memo-settings-toggle');
      toggle.type='checkbox';
      toggle.checked=usesProject();
      toggle.disabled=!!activeJob||waitingForFiles||!!thread.archived;
      toggle.setAttribute('aria-label',t('自动检索本项目资料'));
      toggle.onchange=async()=>{
        const wanted=toggle.checked;
        toggle.disabled=true;
        try{
          if(thread?.id!==owner)return;
          await configureThread({include_project:wanted});
          if(thread?.id===owner)await libraryInline('project');
        }catch(error){
          toggle.checked=!wanted;
          fail(error,'#memo-evidence-status');
        }finally{toggle.disabled=false;}
      };
      row.append(copy,toggle);
      target.append(row);
    }
    const result=source==='library'?await call('memo.library_list'):{items:state.artifacts.filter(a=>a.kind!=='derived'&&!a.temporary_thread&&a.state==='active')};
    if(thread?.id!==owner)return;
    if(!result.items.length){
      target.append(create('p',source==='library'?'主资料库暂无可用处理产物，请先在收件箱完成文献处理。':'当前研究问答项目暂无可用资料，可直接添加文件。'));
      return;
    }
    const search=create('input',null,'field');
    search.type='search';
    search.placeholder=t('搜索资料');
    search.setAttribute('aria-label',t('搜索资料'));
    target.append(search);
    const list=create('div',null,'memo-library-selection');
    target.append(list);
    for(const group of materialGroups(result.items)){
      const row=create('label',null,'memo-library-row'),check=create('input',null,'desktop-batch-check');
      check.type='checkbox';
      check.setAttribute('aria-label',group.title);
      row.dataset.documentId=group.id;
      const matching=()=>state.artifacts.filter(a=>source==='library'?group.items.some(item=>item.id===a.external_id):group.items.some(item=>item.id===a.id));
      const picked=matching(),included=new Set(currentMaterials().map(a=>a.id));
      check.checked=group.items.length===picked.length&&picked.every(a=>included.has(a.id));
      check.indeterminate=!check.checked&&picked.some(a=>included.has(a.id));
      row.dataset.selected=String(check.checked);
      const copy=create('span',null,'memo-library-copy'),title=create('span',group.title);
      title.setAttribute('data-desktop-no-translate','');
      title.title=group.title;
      copy.append(title);
      row.append(check,copy);
      list.append(row);
      check.onchange=async()=>{
        const wanted=check.checked;
        check.disabled=true;
        try{
          let selected=matching();
          if(wanted&&source==='library'){
            const out=await call('memo.library_add',{project:ownerProject,artifacts:group.items.map(a=>({id:a.id,content_hash:a.content_hash}))});
            if(out.failures?.length)throw Error(out.failures[0].code);
            selected=out.items;
          }
          const fresh=await call('memo.thread_get',{thread_id:owner});
          let updated;
          if(wanted)updated=await call('memo.thread_configure',{thread_id:owner,expected_revision:fresh.revision,changes:{artifact_ids:[...new Set([...(fresh.artifact_ids||[]),...selected.map(a=>a.id)])]}});
          else updated=selected.length?await call('memo.material_remove',{thread_id:owner,expected_revision:fresh.revision,artifact_ids:selected.map(a=>a.id)}):fresh;
          if(thread?.id!==owner)return;
          thread=updated;
          await refresh();
          check.indeterminate=false;
          row.dataset.selected=String(wanted);
          status('#memo-evidence-status','');
        }catch(error){
          check.checked=!wanted;
          fail(error,'#memo-evidence-status');
        }
        finally{check.disabled=false;}
      };
    }
    search.oninput=()=>{
      for(const row of list.children)row.hidden=!row.textContent.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase());
    };
  }
  function materialMenu(anchor){
    openMenu(anchor,[['添加文件',pickFiles],['从 Memo 资料库选择',()=>libraryInline()],['从本项目资料中选择',()=>libraryInline('project')]]);
  }

  function initAttachmentUI(){
    const area=chatRoot(),
      shell=chatShell(),
      panel=area.querySelector('#desktop-right-panel'),
      evidence=q('#memo-evidence-panel');
    panel.replaceChildren(evidence);
    panel.setAttribute('aria-label',t('研究资料与知识'));
    panel.classList.add('memo-shared-inspector');
    const sync=()=>{
      const open=shell.app.dataset.right==='true';
      q('#memo-layout').dataset.evidence=String(open);
      q('#memo-evidence-toggle').setAttribute('aria-expanded',String(open));
    };
    new MutationObserver(sync).observe(shell.app,{attributes:true,attributeFilter:['data-right']});
    sync();
    composerPicker=Memorive.modelPicker.bind({button:q('#memo-composer-model'),menu:q('#memo-composer-model-menu'),label:q('#memo-model-label'),
      onSelect:option=>selectConversationModel(option.profileRef||null).catch(fail)});
    action('#memo-add-file',()=>materialMenu(q('#memo-add-file')));
    action('#memo-add-materials',()=>materialMenu(q('#memo-add-materials')));
    action('#memo-composer-scope',()=>{
      showEvidencePanel('materials');
      q('#memo-material-detail').hidden=true;
      q('#memo-material-list').hidden=false;
      renderMaterials();
    });
    new MutationObserver(positionComposerMenu).observe(q('#memo-composer-model-menu'),{attributes:true,attributeFilter:['data-open']});
    window.addEventListener('resize',positionComposerMenu);
    new ResizeObserver(positionComposerMenu).observe(q('#memo-composer'));
    area.addEventListener('dragover',e=>{
      if([...e.dataTransfer?.types||[]].includes('Files')){
        e.preventDefault();
        q('#memo-input-shell').classList.add('is-file-drag');
      }
    });
    area.addEventListener('dragleave',e=>{if(!area.contains(e.relatedTarget))q('#memo-input-shell').classList.remove('is-file-drag');});
    area.addEventListener('drop',e=>{
      e.preventDefault();
      q('#memo-input-shell').classList.remove('is-file-drag');
    });
  }


  init();
})();

