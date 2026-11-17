/* Research runs shared by several pages: starting, following and cancelling literature discovery runs, their
   preferences and feedback (research.*), and their place on the current task, messages and library pages.
   Publishes window.__Desktop_RESEARCH__. */
(() => {
  'use strict';
  if (window.__Desktop_RESEARCH__) return;
  const call = (method, params = {}) => window.__Desktop_SERVICE__.call(method, params);
  const make = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };
  const labels = { CORE: '重点跟进', ADJACENT: '相邻拓展', BRIDGE: '跨方向连接', HORIZON: '远期探索', COVERAGE_REPAIR: '补足盲区' };
  const states = { QUEUED: '等待中', RUNNING: '运行中', SUCCEEDED: '已完成', PARTIAL: '部分完成', FAILED: '失败', CANCELLED: '已取消', INTERRUPTED: '已中断' };
  const errors = { RESEARCH_SOURCE_REQUIRED: '请先在外部数据来源中选择文献来源', RESEARCH_TOPIC_REQUIRED: '请输入检索主题', RESEARCH_ALREADY_RUNNING: '已有研究任务正在运行', ALL_LITERATURE_SOURCES_FAILED: '文献来源请求失败，请检查网络后重试', RESEARCH_IDEMPOTENCY_CONFLICT: '请求已变化，请重新运行' };
  Object.assign(errors,{REPORT_MODEL_REQUIRED:'请先在流程模型映射中选择报告模型',REPORT_MODEL_UNAVAILABLE:'所选报告模型不可用，请检查模型设置',REPORT_NO_LIBRARY_CHANGE:'该周期没有可汇总的新成果',REPORT_MODEL_EXECUTION_FAILED:'报告模型执行失败，请检查任务记录'});
  let pane = null, workspace = null, timer = null, lastFocus = null;
  function close() {
    clearTimeout(timer);
    pane?.remove();
    workspace?.removeAttribute('data-research-open');
    pane = null;
    workspace = null;
    lastFocus?.focus();
  }
  function openShell(title, origin) {
    close();
    lastFocus=document.activeElement;
    const root = origin?.closest('[id$="-preview"]') || [...document.querySelectorAll('[id$="-preview"]')].find(x => x.getBoundingClientRect().width > 0);
    workspace = root?.querySelector('.desktop-workspace');
    if (!workspace) throw new Error('当前页面不可用');
    workspace.setAttribute('data-research-open', 'true');
    pane = make('aside',null,'desktop-research-pane');
    pane.setAttribute('aria-label',title);
    const head=make('header',null,'desktop-research-head');
    const back=make('button','‹','icon-button');
    back.type='button';
    back.setAttribute('aria-label','返回');
    back.onclick=close;
    const heading=make('h3',title);
    const actions=make('div',null,'desktop-research-actions');
    head.append(back,heading,actions);
    const body=make('div',null,'desktop-research-body');
    pane.append(head,body);
    workspace.append(pane);
    back.focus();
    pane.addEventListener('keydown',e=>{if(e.key==='Escape'){
      e.stopPropagation();
      close();
    }});
    return {body,actions,pane};
  }
  function button(text, fn, cls='soft-button') {
    const b=make('button',text,cls);
    b.type='button';
    b.onclick=fn;
    return b;
  }
  function field(label, type, value) {
    const wrap=make('label',null,'field-label');
    wrap.dataset.helpLabel=label;
    wrap.append(make('span',label));
    const input=make('input',null,'field');
    input.type=type;
    input.value=value;
    wrap.append(input);
    return {wrap,input};
  }
  async function open(kind='discovery',origin=null) {
    if(kind==='discovery') return window.__Desktop_EXTERNAL_SOURCES__?.load();
    const view=openShell('周期报告',origin);
    const status=make('p',null,'desktop-research-status');
    status.setAttribute('role','status');
    const history=make('section',null,'desktop-research-history');
    let periodSelect;
    try {
      const label=make('label',null,'field-label');
      label.append(make('span','上一完整周期'));
      periodSelect=make('select',null,'select');
      for(const [v,text] of [['daily','日报'],['weekly','周报'],['monthly','月报']]){
        const o=make('option',text);
        o.value=v;
        periodSelect.append(o);
      }
      periodSelect.value='weekly';
      label.append(periodSelect);
      view.body.append(label);
      const run=button('生成',async()=>{
        try {
          run.disabled=true;
          status.textContent=kind==='discovery'?'正在检索所选来源…':'正在生成报告…';
          const result=await call('research.start',{kind,request_id:'desktop-'+crypto.randomUUID(),...(kind==='report'?{period:periodSelect.value}:{})});
          status.textContent=states[result.status]||result.status;
          await refresh();
        } catch(e){status.textContent=errors[e.message] || '运行未开始，请检查来源、主题和网络设置';}
        finally{run.disabled=false;}
      },'primary-button');
      view.body.append(run,status,history);
      async function refresh() {
        if(!view.pane.isConnected)return;
        const value=await call('research.state');
        if(!view.pane.isConnected)return;
        history.replaceChildren(make('h4','最近运行'));
        const rows=value.runs.filter(x=>x.kind===kind);
        if(rows.length)status.textContent=(states[rows[0].status]||rows[0].status)+(rows[0].error_code?' · '+(errors[rows[0].error_code]||rows[0].error_code):'');
        if(!rows.length)history.append(make('p','暂无运行记录','desktop-research-muted'));
        for(const row of rows) {
          const item=button('',()=>openResult(row.run_id,origin),'desktop-research-run');
          item.append(make('strong',row.title),make('small',new Date(row.created_at).toLocaleString()+' · '+(states[row.status]||row.status)));
          history.append(item);
          if(row.status==='RUNNING'||row.status==='QUEUED')history.append(button('取消',async()=>{
            await call('research.cancel',{run_id:row.run_id});
            await refresh();
          },'text-button'));
        }
        clearTimeout(timer);
        if(value.active_run_id)timer=setTimeout(()=>refresh().catch(()=>{}),900);
      }
      await refresh();
    } catch(e){
      status.textContent='无法读取研究设置，请重新打开';
      view.body.append(status);
    }
  }
  async function openResult(runId,origin=null) {
    const state=await call('research.get',{run_id:runId});
    if(!state.library_locator)throw new Error('结果尚未写入资料库');
    close();
    return window.__Desktop_LIBRARY_BRIDGE__.openArtifactByLocator(state.library_locator);
  }
  async function renderResult(runId,body) {
    body.dataset.researchRunId=runId;
    body.replaceChildren(make('p','正在读取…'));
    const state=await call('research.get',{run_id:runId});
    if(!body.isConnected||body.dataset.researchRunId!==runId)return;
    body.replaceChildren();
    if(!state.result){
      body.append(make('p','结果不可用'));
      if(state.delivery_recoverable)body.append(button('恢复已保存结果',async()=>{
        await call('research.repair_delivery',{run_id:runId});
        await renderResult(runId,body);
      }));
      return;
    }
    body.append(make('small','校验值：'+state.result.sha256,'desktop-research-muted'));
    if(state.kind==='ai_briefing'){
      window.__MEMO_RESEARCH_HOME__.renderAIResult(body,state.result,runId);
      body.append(button('查看关联任务',()=>openTask(runId)));
      return;
    }
    if(state.result.recommendations) {
      if(!state.result.recommendations.length)body.append(make('p','暂无符合条件的新候选'));
      for(const row of state.result.recommendations) {
        const card=make('article',null,'desktop-research-paper');
        card.append(make('h4',row.position+'. '+row.title),make('p',row.authors.join('、')),make('small',row.sources.join(' / ')+' · '+(row.published_date||'日期未提供')));
        if(row.abstract){
          const more=make('details');
          more.append(make('summary','摘要'),make('p',row.abstract));
          card.append(more);
        }
        card.dataset.candidateId=row.candidate_id;
        card.append(make('p',row.identity_key,'desktop-research-muted'));
        if(row.direction_name)card.append(make('p','研究方向：'+row.direction_name+' · r'+row.direction_revision,'desktop-research-muted'));
        if(row.journal||row.platform)card.append(make('small',(row.journal||row.platform)+' · '+(row.record_type||'')));
        if(row.recommendation_reason)card.append(make('p',row.recommendation_reason));
        const sources=make('div',null,'desktop-research-feedback-row');
        for(const link of row.links||[]){
          const a=make('a',link.label);
          a.href=link.url;
          a.target='_blank';
          a.rel='noopener noreferrer';
          a.addEventListener('click',async event=>{
            event.preventDefault();
            try{await call('research.open_source',{run_id:runId,candidate_id:row.candidate_id,url:link.url});}catch(_){a.title='来源链接未能打开';}
          });
          sources.append(a);
        }
        card.append(sources);
        const relations=row.relation_comparison;
        if(relations)card.append(make('p',relations.library_new==='FALSE'?'本地库有相同标识文献':relations.library_new==='UNKNOWN'?'本地身份资料不完整；新颖性尚未评估':'当前本地结构化标识中未见相同文献','desktop-research-muted'));
        body.append(card);
        if(row.direction_revision)attachCandidateActions(runId,row,card);
      }
      if(state.result.source_failures.length)body.append(make('p','部分来源未完成：'+state.result.source_failures.map(x=>x.source+' · '+(errors[x.error_code]||'连接或数据返回未完成，请检查来源设置')).join('；')));
    } else {
      const text=make('pre',state.result.markdown,'desktop-research-report');
      body.append(text);
    }
    if(state.notification_error)body.append(button('修复结果投递',async()=>{
      await call('research.repair_delivery',{run_id:runId});
      await renderResult(runId,body);
    }));
    body.append(button('查看关联任务',()=>openTask(runId)));
  }
  const decisionLabels={NOT_FOR_THIS_DIRECTION:'不适合此方向',GLOBALLY_USELESS:'不再推荐此文献',ALREADY_READ:'已经读过',IN_LIBRARY:'已在我的资料库',
    SNOOZE:'暂缓推荐',NO_LEGAL_FULLTEXT:'无法合法获取全文',CARD_ERROR:'候选内容有误',MANIFESTATION_NOT_USEFUL:'此版本不适合'};
  async function renderFeedback(body){
    const [state,interest]=await Promise.all([call('research.feedback_state'),call('research.interaction_state')]);
    const interestStatus=make('p',null,'desktop-research-muted');
    if(interest.status==='OBSERVATIONAL_ONLY')interestStatus.append(make('span','近期互动记录已开启'),make('span',' · '+interest.events.length+' '),make('span','条记录'));
    else interestStatus.append(make('span',interest.display_status));
    body.replaceChildren(interestStatus);
    const revoked=new Set(state.events.filter(e=>['DECISION_REVOKED','MANUAL_RESTORE'].includes(e.event_type)).map(e=>e.target_event_id));
    for(const event of state.events.filter(e=>e.event_type==='DECISION_RECORDED'&&!revoked.has(e.event_id))){
      const row=make('div',null,'desktop-research-feedback-row');
      row.append(make('span',decisionLabels[event.decision_type]||'已记录反馈'),make('time',new Date(event.event_time).toLocaleString()));
      row.append(button('撤销',async()=>{
        await call('research.revoke',{decision_event_id:event.event_id,request_id:'undo-'+crypto.randomUUID()});
        await renderFeedback(body);
      }));
      body.append(row);
    }
    if(!state.events.some(e=>e.event_type==='DECISION_RECORDED'))body.append(make('p','暂无反馈'));
  }
  function attachCandidateActions(runId,row,card){
    let exposure=null,pending=null;
    const expose=()=>exposure?Promise.resolve(exposure):(pending||(pending=call('research.expose',{run_id:runId,candidate_id:row.candidate_id,
      request_id:'visible-'+runId+'-'+row.candidate_id}).then(x=>exposure=x).finally(()=>pending=null)));
    const observer=new IntersectionObserver(entries=>{
      if(!card.isConnected){
        observer.disconnect();
        return;
      }
      if(document.visibilityState==='visible'&&entries.some(e=>e.isIntersecting))expose().then(()=>observer.disconnect()).catch(()=>{});
    });
    observer.observe(card);
    const actions=make('div',null,'desktop-research-feedback-row'),select=make('select',null,'select');
    select.append(make('option','选择反馈'));
    select.firstChild.value='';
    for(const [key,label] of Object.entries(decisionLabels)){
      if(key==='CARD_ERROR')continue;
      const option=make('option',label);
      option.value=key;
      select.append(option);
    }
    const until=make('input',null,'field');
    until.type='datetime-local';
    until.hidden=true;
    until.setAttribute('aria-label','暂缓至');
    select.onchange=()=>{until.hidden=select.value!=='SNOOZE';};
    const status=make('span',null,'desktop-research-muted');
    const send=button('确认反馈',async()=>{
      if(!select.value)return;
      const options={};
      if(select.value==='GLOBALLY_USELESS'){
        if(!confirm('确认在所有研究方向中排除此文献？可以在反馈记录中撤销。'))return;
        options.global_confirmation=true;
      }
      if(select.value==='SNOOZE'){
        if(!until.value){
          status.textContent='请选择暂缓到期时间';
          return;
        }
        options.cooldown_until=new Date(until.value).toISOString();
      }
      if(['NO_LEGAL_FULLTEXT','MANIFESTATION_NOT_USEFUL'].includes(select.value))options.manifestation_id=row.manifestation_id;
      send.disabled=true;
      try{
        const shown=await expose();
        const decision=await call('research.feedback',{exposure_event_id:shown.event_id,decision_type:select.value,request_id:'feedback-'+crypto.randomUUID(),...options});
        status.textContent='已记录，将用于下一批';
        actions.append(button('撤销',async event=>{
          const undoButton=event.currentTarget;
          await call('research.revoke',{decision_event_id:decision.event_id,request_id:'undo-'+crypto.randomUUID()});
          undoButton.remove();
          status.textContent='已撤销';
        }));
      }catch(_){status.textContent='反馈未保存，请检查选择或稍后重试';}finally{send.disabled=false;}
    });
    actions.append(select,until,send,status);
    card.append(actions);
  }
  async function openTask(runId){
    await window.__Desktop_INTEGRATED_APP__.activate('current-task');
    await window.__Desktop_CURRENT_TASK_BRIDGE__?.refresh?.();
    await window.__Desktop_CURRENT_TASK_BRIDGE__?.selectRun?.(runId);
    const tasks=await call('research.task_list');
    const row=tasks.rows.find(r=>r.run_id===runId);
    window.__Desktop_TASKS_UI__?.setTaskMode?.(row?.history?'history':'current');
    window.__Desktop_TASKS_UI__?.openTaskByRun?.(runId);
  }
  function taskActions(runId,binding,container){
    if(binding.workflow_kind==='RESEARCH_AI_BRIEFING')return window.__MEMO_RESEARCH_HOME__.taskActions(runId,binding,container);
    const status=make('span',null,'desktop-research-muted');
    const isReport=binding.workflow_kind==='RESEARCH_REPORT';
    if(binding.source_status==='PARTIAL')container.append(make('span','部分完成 · 请查看结果中的来源缺口或投递情况','desktop-research-muted'));
    if(binding.result_locator)container.append(button('查看结果',()=>openResult(runId)));
    if(binding.delivery_repair_available)container.append(button('修复结果投递',async event=>{
      event.currentTarget.disabled=true;
      try{
        await call('research.repair_delivery',{run_id:runId});
        await openTask(runId);
      }
      catch(_){
        status.textContent='投递未恢复；保留原结果，没有重新检索。';
        event.currentTarget.disabled=false;
      }
    }));
    if(['QUEUED','RUNNING'].includes(binding.source_status)){
      container.append(button(isReport?'取消生成':'取消检索',async event=>{
        const action=event.currentTarget;
        action.disabled=true;
        try{
          await call('research.cancel',{run_id:runId});
          status.textContent='正在取消，已获取记录会保留';
        }catch(_){
          status.textContent='取消未成功，请刷新任务状态';
          action.disabled=false;
        }
      }));
    }else{
      container.append(button(isReport?'重新生成报告':'重新检索',async event=>{
        const action=event.currentTarget;
        if(!window.__Desktop_UI_CONTROLS__?.confirmRestart(action,'restart:'+runId))return;
        action.disabled=true;
        try{
          const receipt=await call('research.start',{kind:isReport?'report':'discovery',...(isReport?{period:binding.period,regenerate_from:runId}:{}),request_id:'manual-'+crypto.randomUUID()});
          await openTask(receipt.run_id);
        }
        catch(_){
          status.textContent=isReport?'未能生成报告，请稍后重试':'未能开始检索，请检查保存的方向与来源';
          action.disabled=false;
        }
      }));
      if(binding.source_status==='FAILED')container.append(make('span',isReport?'报告未完成':'检索未完成','desktop-research-muted'));
    }
    container.append(status);
  }
  async function updatePreference(patch) {
    const current=await call('research.config_get');
    const saved=await call('research.config_save',{config:{...current.config,...patch},expected_revision:current.revision});
    window.dispatchEvent(new CustomEvent('desktop:research-preferences-saved',{detail:saved}));
    return saved;
  }
  const helpCopies={
    '自动接收':'开启后按已保存的研究方向定期检索；关闭不删除历史报告。',
    '每批期望篇数':'每批希望收到的文献数，1–100 篇，整数。来源不足或多样性约束生效时，实际篇数可能更少。',
    '检索间隔（天）':'两次自动检索之间的天数，1–365，整数。关闭程序期间继续计时；重新打开后补一次已到期检索。',
    '方向名称':'用于识别和管理这个研究方向；改名不改变检索内容。',
    '检索表达式':'发送给已启用文献来源的检索内容。修改后，下一批使用新表达式。',
    '关键词':'以逗号分隔的方向关键词，作为方向描述保留；当前不会自动扩写来源检索式。',
    '参考文献标识':'以逗号分隔的 DOI 或文献标识，作为方向参考保留；不会自动下载全文。',
    '重点跟进':'围绕当前核心研究方向分配推荐篇数。权重 0–1000，整数；0 表示不向这条通道分配。',
    '相邻拓展':'为相近问题或相邻领域分配推荐篇数。权重 0–1000，整数；需为研究方向勾选这条通道。',
    '跨方向连接':'为不同研究方向之间的联系分配推荐篇数。权重 0–1000，整数；不是匹配概率。',
    '远期探索':'为较远期的探索方向保留推荐篇数。权重 0–1000，整数；不会自动扩展到未设置的主题。',
    '补足盲区':'为希望补充了解的方向分配推荐篇数。权重 0–1000，整数；各通道按相对权重分配，无需合计为 100。',
    '同一作者 · 最多篇数':'每批同一作者最多出现的篇数，1–100，整数；留空不限制。不足时不会突破该限制凑数。',
    '同一机构 · 最多篇数':'每批同一机构最多出现的篇数，1–100，整数；留空不限制。',
    '同一期刊 · 最多篇数':'每批同一期刊最多出现的篇数，1–100，整数；留空不限制。',
    '同一方向 · 最多篇数':'每批同一研究方向最多出现的篇数，1–100，整数；留空不限制。',
    '同一方向 · 最高占比（%）':'单个研究方向在每批推荐中的最高比例，1–100%，整数；留空不限制。与最多篇数同时设置时，采用更严格的约束。',
    '主题':'未建立独立研究方向时使用的基础检索主题；选填。',
    '显示已看过的文献':'允许以前已经推荐过的文献再次进入结果；关闭可减少重复。',
    '记录近期互动':'在本机保留互动记录。当前记录不会用于自动兴趣推荐。',
  };
  function installHelp(block){
    for(const host of block.querySelectorAll('.field-label,.desktop-research-check,.switch-copy')){
      if(host.dataset.helpInstalled)continue;
      const label=host.dataset.helpLabel||(host.querySelector('span,strong')?.textContent||host.textContent).trim();
      if(!helpCopies[label])continue;
      host.dataset.helpInstalled='true';
      let title=host;
      if(host.classList.contains('field-label')){
        title=host.querySelector(':scope > span');
        title.classList.add('desktop-setting-label');
      }
      window.__Desktop_SETTINGS_CONTROLS__.help(title,label,helpCopies[label]);
      const input=host.querySelector('input');
      if(input?.type==='number'){
        input.title=input.min+'–'+input.max+' · '+(window.__Desktop_INTEGRATED_APP__?.translate?.('整数')||'整数');
        input.inputMode='numeric';
        input.addEventListener('keydown',event=>{if(['e','E','+','-','.'].includes(event.key))event.preventDefault();});
      }
    }
  }
  async function mountPreferences(container) {
    const block=make('section',null,'desktop-research-preferences');
    block.dataset.researchPreferences='true';
    const summary=make('div',null,'switch-row');
    const caption=make('span',null,'switch-copy');
    caption.append(make('strong','自动接收'));
    const enabled=button('',event=>{
      event.preventDefault();
      event.stopPropagation();
      enabled.setAttribute('aria-checked',String(enabled.getAttribute('aria-checked')!=='true'));
      window.__Desktop_SETTINGS_BRIDGE__.syncSaveState();
    },'switch');
    enabled.setAttribute('role','switch');
    enabled.setAttribute('aria-label','接收外部文献');
    enabled.disabled=true;
    const headActions=make('span',null,'desktop-settings-source-head-actions');
    headActions.append(enabled);
    summary.append(caption,headActions);
    block.append(summary);
    const body=make('div',null,'desktop-research-preference-fields');
    const status=make('p',null,'desktop-research-status');
    status.setAttribute('role','status');
    block.append(body);
    container.append(block);
    try {
      const current=await call('research.config_get');
      if(!block.isConnected)return;
      const c=current.config;
      enabled.setAttribute('aria-checked',String(c.passive_enabled));
      enabled.disabled=false;
      const topic=field('主题','text',c.topic);
      topic.input.placeholder='选填';
      const count=field('每批期望篇数','number',c.count);
      count.input.min=1;
      count.input.max=100;
      count.input.step=1;
      count.input.required=true;
      const interval=field('检索间隔（天）','number',c.interval_hours/24);
      interval.input.min=1;
      interval.input.max=365;
      interval.input.step=1;
      interval.input.required=true;
      const recent=make('label',null,'desktop-research-check'),recentInput=make('input');
      recentInput.type='checkbox';
      recentInput.checked=c.use_recent_interests;
      recentInput.dataset.recentInterests='true';
      recent.append(recentInput,make('span','记录近期互动'));
      recent.title='仅在本机记录；不会将自动分析当作主动兴趣。自动兴趣推荐尚未接入。';
      const seen=make('label',null,'desktop-research-check'),seenInput=make('input');
      seenInput.type='checkbox';
      seenInput.checked=c.include_seen;
      seen.append(seenInput,make('span','显示已看过的文献'));
      const directionSection=make('section',null,'desktop-research-direction-list');
      const directionHead=make('div',null,'desktop-research-section-head');
      directionHead.append(make('h4','研究方向'));
      directionSection.append(directionHead);
      const directionRows=[];
      const sync=()=>window.__Desktop_SETTINGS_BRIDGE__.syncSaveState();
      const watch=element=>{
        for(const eventName of ['input','change'])element.addEventListener(eventName,sync);
        return element;
      };
      function addDirection(initial=null){
        let original=initial ? structuredClone(initial) : {id:'direction-'+crypto.randomUUID(),revision:1,name:'',query:'',channels:['CORE'],keywords:[],seed_ids:[]};
        const card=make('details',null,'desktop-research-direction');
        card.open=!initial;
        card.dataset.directionId=original.id;
        const head=make('summary'),
          caption=make('strong',original.name||'新研究方向'),
          actions=make('span',null,'desktop-settings-source-head-actions');
        const remove=button('删除',event=>{
          event.preventDefault();
          event.stopPropagation();
          window.__Desktop_SETTINGS_CONTROLS__.pendingRemoval(card,()=>{
            card.remove();
            empty.hidden=directionRows.some(r=>r.card.isConnected);
            sync();
          },sync);
        });
        remove.dataset.researchDeleteDirection='true';
        remove.setAttribute('aria-label','移除研究方向');
        actions.append(remove,make('span','›','desktop-settings-disclosure-icon'));
        head.append(caption,actions);
        card.append(head);
        const fields=make('div',null,'desktop-research-preference-fields');
        const name=field('方向名称','text',original.name),query=field('检索表达式','text',original.query);
        name.input.required=true;
        query.input.required=true;
        const keywords=field('关键词','text',original.keywords.join(', '));
        keywords.input.placeholder='以逗号分隔';
        const seeds=field('参考文献标识','text',original.seed_ids.join(', '));
        seeds.input.placeholder='DOI 或文献标识，以逗号分隔';
        const channels=make('div',null,'desktop-research-channel-options'),channelInputs={};
        for(const [key,label] of Object.entries(labels)){
          const wrap=make('label',null,'desktop-research-check'),check=make('input');
          check.type='checkbox';
          check.checked=original.channels.includes(key);
          wrap.append(watch(check),make('span',label));
          channels.append(wrap);
          channelInputs[key]=check;
        }
        name.input.addEventListener('input',()=>caption.textContent=name.input.value||'新研究方向');
        [name.input,query.input,keywords.input,seeds.input].forEach(watch);
        const advanced=make('details',null,'desktop-research-advanced');
        advanced.append(make('summary','更多选项'),keywords.wrap,seeds.wrap,channels);
        fields.append(name.wrap,query.wrap,advanced);
        card.append(fields);
        directionSection.append(card);
        const split=value=>value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);
        directionRows.push({card,accept:saved=>{
          original=structuredClone(saved);
          initial=saved;
        },collect:()=>{
          const semanticChanged=query.input.value.trim()!==original.query||JSON.stringify(split(keywords.input.value))!==JSON.stringify(original.keywords)||JSON.stringify(split(seeds.input.value))!==JSON.stringify(original.seed_ids);
          return {id:original.id,revision:original.revision+(initial&&semanticChanged?1:0),name:name.input.value.trim(),query:query.input.value.trim(),
            keywords:split(keywords.input.value),seed_ids:split(seeds.input.value),channels:Object.keys(channelInputs).filter(k=>channelInputs[k].checked)};
        }});
      }
      for(const direction of c.directions)addDirection(direction);
      const empty=make('p','暂无研究方向','desktop-research-muted');
      empty.hidden=c.directions.length>0;
      directionSection.append(empty);
      const add=button('添加研究方向',()=>{
        empty.hidden=true;
        addDirection();
        sync();
      });
      directionHead.append(add);
      const policy=make('details',null,'desktop-research-advanced');
      policy.append(make('summary','视野与推荐偏好'));
      const weights={},weightGrid=make('div',null,'desktop-research-grid');
      for(const [key,label] of Object.entries(labels)){
        const control=field(label,'number',c.weights[key]);
        control.input.min=0;
        control.input.max=1000;
        control.input.step=1;
        control.input.required=true;
        control.input.title='每批推荐的相对配比';
        weights[key]=watch(control.input);
        weightGrid.append(control.wrap);
      }
      policy.append(weightGrid);
      const limits={},limitSection=make('details',null,'desktop-research-advanced');
      limitSection.append(make('summary','多样性设置'));
      const limitGrid=make('div',null,'desktop-research-grid');
      limitSection.append(limitGrid);
      policy.append(limitSection);
      for(const [key,label] of Object.entries({author:'同一作者',institution:'同一机构',journal:'同一期刊',direction:'同一方向'})){
        const control=field(label+' · 最多篇数','number',c.exposure_limits[key]??'');
        control.input.placeholder='不限制';
        control.input.min=1;
        control.input.max=100;
        control.input.step=1;
        limits[key]=watch(control.input);
        limitGrid.append(control.wrap);
      }
      const rawShare=c.exposure_limits.direction_share==null?null:c.exposure_limits.direction_share*100;
      const displayShare=rawShare==null?'':Math.abs(rawShare-Math.round(rawShare))<1e-8?Math.round(rawShare):rawShare;
      const share=field('同一方向 · 最高占比（%）','number',displayShare);
      share.input.placeholder='不限制';
      share.input.min=1;
      share.input.max=100;
      share.input.step=1;
      watch(share.input);
      limitGrid.append(share.wrap);
      const feedback=make('details',null,'desktop-research-advanced');
      feedback.append(make('summary','反馈记录'));
      const feedbackBody=make('div');
      feedback.append(feedbackBody);
      feedback.addEventListener('toggle',()=>{if(feedback.open)void renderFeedback(feedbackBody);});
      async function refreshInterestState(){
        if(feedback.open)await renderFeedback(feedbackBody);
      }
      const collect=()=>({topic:topic.input.value.trim(),count:Number(count.input.value),interval_hours:Number(interval.input.value)*24,
        include_seen:seenInput.checked,passive_enabled:enabled.getAttribute('aria-checked')==='true',
        use_recent_interests:recentInput.checked,directions:directionRows.filter(r=>directionSection.contains(r.card)).map(r=>r.collect()),
        weights:Object.fromEntries(Object.entries(weights).map(([k,v])=>[k,Number(v.value)])),
        exposure_limits:{...Object.fromEntries(Object.entries(limits).map(([k,v])=>[k,v.value===''?null:Number(v.value)])),direction_share:share.input.value===''?null:Number(share.input.value)/100}});
      let baseline=JSON.stringify(collect());
      const originals=new WeakMap();
      const remember=()=>block.querySelectorAll('input[type="number"]').forEach(input=>{if(!originals.has(input))originals.set(input,input.value);});
      const validate=()=>{
        if(block.querySelector('.desktop-pending-removal'))return false;
        const invalid=[...block.querySelectorAll('input')].filter(input=>{
          if(input.type==='number' && originals.get(input)===input.value)return false;
          return !input.checkValidity();
        });
        const sum=Object.values(weights).reduce((total,input)=>total+Number(input.value),0);
        const missingChannel=directionRows.some(r=>r.card.isConnected&&!r.collect().channels.length);
        status.textContent=invalid.length?'请按提示范围填写，数值仅支持整数':sum<=0?'至少一个推荐方向的权重需要大于 0':missingChannel?'每个研究方向至少选择一条推荐通道':'';
        for(const input of block.querySelectorAll('input'))input.setAttribute('aria-invalid',String(invalid.includes(input)));
        return !invalid.length && sum>0 && !missingChannel;
      };
      block.__isDirty=()=>Boolean(block.querySelector('.desktop-pending-removal'))||JSON.stringify(collect())!==baseline;
      window.__Desktop_SETTINGS_BRIDGE__.registerSaveParticipant('research-preferences',{
        isDirty:()=>block.isConnected && block.__isDirty(),
        validate:()=>!block.isConnected||validate(),
        save:async()=>{
          const saved=await updatePreference(collect());
          for(const row of directionRows){
            const value=saved.config.directions.find(d=>d.id===row.card.dataset.directionId);
            if(value)row.accept(value);
          }
          baseline=JSON.stringify(collect());
          block.querySelectorAll('input[type="number"]').forEach(input=>originals.set(input,input.value));
          status.textContent='已保存';
          await refreshInterestState();
        },
        discard:async()=>{
          const parent=block.parentElement;
          block.remove();
          if(parent)await mountPreferences(parent);
        }
      });
      const basic=make('div',null,'desktop-research-grid');
      basic.append(interval.wrap,count.wrap);
      const options=make('details',null,'desktop-research-advanced');
      options.append(make('summary','其他设置'),topic.wrap,seen,recent);
      const discoverActions=make('div',null,'desktop-research-feedback-row');
      const directionSelect=make('select',null,'select');
      directionSelect.setAttribute('aria-label','检索方向');
      const allDirections=make('option','全部已保存方向');
      allDirections.value='';
      directionSelect.append(allDirections);
      for(const direction of c.directions){
        const option=make('option',direction.name);
        option.value=direction.id;
        directionSelect.append(option);
      }
      discoverActions.append(directionSelect);
      for(const [objective,caption] of [['latest','最新文献'],['related','高度相关文献']]){
        const launch=button(caption,async()=>{
          if(block.__isDirty()||[...document.querySelectorAll('[data-source-id]')].some(x=>x.__isDirty?.())){
            status.textContent='请先保存或撤销修改';
            return;
          }
          launch.disabled=true;
          try{
            const receipt=await call('research.discover',{request_id:'discovery-'+crypto.randomUUID(),objective,...(directionSelect.value?{direction_id:directionSelect.value}:{})});
            status.textContent='检索已开始';
            await openTask(receipt.run_id);
          }catch(error){status.textContent=errors[error.message]||'检索未开始，请检查已保存方向和来源';}
          finally{launch.disabled=false;}
        });
        launch.dataset.discoveryObjective=objective;
        if(objective==='latest')launch.title='近 90 天首次发表的文献';
        discoverActions.append(launch);
      }
      body.append(basic,directionSection,discoverActions,policy,options,feedback,status);
      remember();
      installHelp(block);
      [topic.input,count.input,interval.input,seenInput,recentInput].forEach(watch);
      block.addEventListener('input',validate);
      new MutationObserver(()=>installHelp(block)).observe(directionSection,{childList:true,subtree:true});
    }catch(_){body.append(make('p','外部文献设置读取失败，请重新打开')); }
  }
  let noticeBaseline=null;
  const notice=document.getElementById('settings-notify-literature');
  const noticeDirty=()=>noticeBaseline!==null && (notice.getAttribute('aria-checked')==='true')!==noticeBaseline;
  async function syncNotice(force=false) {
    if(!notice || (force!==true && noticeDirty()))return;
    try {
      const current=await call('research.config_get');
      if(force!==true && noticeDirty())return;
      noticeBaseline=current.config.push_enabled;
      notice.setAttribute('aria-checked',String(noticeBaseline));
      notice.disabled=false;
    }
    catch(_){notice.disabled=true;}
  }
  if(notice) {
    notice.addEventListener('click',event=>{
      event.preventDefault();
      event.stopImmediatePropagation();
      notice.setAttribute('aria-checked',String(notice.getAttribute('aria-checked')!=='true'));
      window.__Desktop_SETTINGS_BRIDGE__.syncSaveState();
    },true);
    window.__Desktop_SETTINGS_BRIDGE__.registerSaveParticipant('literature-notification',{
      isDirty:noticeDirty,
      save:async()=>{
        const saved=await updatePreference({push_enabled:notice.getAttribute('aria-checked')==='true'});
        noticeBaseline=saved.config.push_enabled;
      },
      discard:()=>syncNotice(true)
    });
  }
  document.querySelector('[data-category="notice"]')?.addEventListener('click',syncNotice);
  window.addEventListener('desktop:research-preferences-saved',syncNotice);
  window.addEventListener('pywebviewready',syncNotice);
  window.__Desktop_RESEARCH__={open,openResult,renderResult,openTask,taskActions,close,mountPreferences,syncNotice};

  // Reports are scheduled from completed material progress, not a work-log command.
  window.addEventListener('desktop:research-published',()=> {
    window.__Desktop_LIBRARY_BRIDGE__?.refresh?.();
    window.__Desktop_MESSAGES_BRIDGE__?.refresh?.();
    window.__Desktop_CURRENT_TASK_BRIDGE__?.refresh?.();
    window.__Desktop_WORK_LOG_BRIDGE__?.refresh?.();
  });
})();

