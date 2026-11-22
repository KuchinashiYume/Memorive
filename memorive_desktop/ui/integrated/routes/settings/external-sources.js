/* The settings page's external data panel: model scores, model prices, exchange rates and literature sources, with
   editing, saving and per-source refresh (settings.external_sources_*, research.config_get, research.source_refresh).
   Takes part in the page's shared save and revert. Publishes Memorive.externalSources. */
(() => {
  'use strict';
  const root = document.getElementById('desktop-settings-preview');
  if (!root) return;
  const q = s => root.querySelector(s);
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const call = (method, params = {}) => Memorive.settingsBridge.call(method, params);
  // These controls have their own persistence/invocation scope. Do not let
  // legacy form-wide listeners treat a reference edit or exam tier as a model
  // mapping change, which would otherwise disable immediate examination.
  for (const control of [q('[data-panel="external-data"]'), q('[data-panel="literature"]'), q('#settings-exam-tier')]) {
    for (const eventName of ['input', 'change']) {
      control.addEventListener(eventName, event => event.stopPropagation());
    }
  }
  let state, sequence = 0, busy = false, loadGeneration = 0;
  const headerRefresh = q('#settings-external-refresh-all');
  headerRefresh.addEventListener('click', async () => {
    if (busy) return;
    if ([...q('#settings-external-sources').querySelectorAll('details')].some(card => card.__isDirty?.())) {
      status('请先保存');
      return;
    }
    busy = true;
    headerRefresh.disabled = true;
    headerRefresh.setAttribute('aria-busy','true');
    const controls = [...q('#settings-external-sources').querySelectorAll('button,input,select')].map(x=>[x,x.disabled]);
    controls.forEach(([x])=>x.disabled=true);
    status('正在更新');
    try {
      const current = await call('settings.external_sources_get');
      const enabled = current.sources.filter(x=>x.config.enabled);
      const receipts = [];
      // Sequential calls preserve each source's existing lock, receipt and quota.
      for (const item of enabled) receipts.push(await call('settings.external_sources_refresh',
        {source_id:item.config.source_id, expected_revision:current.revision}));
      await load();
      refreshCurrent();
      const failures=receipts.filter(x=>x.status!=='PASS');
      status(!enabled.length ? '未启用来源' : !failures.length ? '数据已更新' :
        `已更新 ${receipts.length-failures.length}/${receipts.length} · ${failureCopy(failures[0])}`);
    } catch (_) { status('更新失败 · 请检查网络'); }
    finally {
      busy=false;
      headerRefresh.disabled=false;
      headerRefresh.removeAttribute('aria-busy');
      controls.forEach(([x,disabled])=>{if(x.isConnected)x.disabled=disabled;});
    }
  });
  const status = text => {
    q('#settings-external-status').textContent = text;
    q('#settings-literature-status').textContent = text;
    if (text && !text.startsWith('正在更新')) Memorive.settingsBridge.notify('外部数据 · '+text);
  };
  const failureCopy = receipt => {
    const reason = receipt?.reason || '';
    const retained = receipt?.previous_snapshot_preserved ? '已保留旧数据' : '暂无可用缓存';
    if (reason === 'EXTERNAL_SOURCE_CREDENTIAL_NOT_CONFIGURED') return '请配置凭据变量';
    if (/HTTP_(401|403)$/.test(reason)) return '来源拒绝访问 · '+retained;
    if (reason === 'EXTERNAL_SOURCE_NONPUBLIC_ADDRESS') return '更新失败 · 地址不可用';
    if (reason === 'EXTERNAL_METADATA_TLS_CERTIFICATE') return '证书验证失败，请检查系统证书或代理 · '+retained;
    if (reason === 'EXTERNAL_METADATA_TLS_CONNECTION') return '安全连接失败，请检查网络或代理 · '+retained;
    if (reason === 'EXTERNAL_METADATA_TIMEOUT') return '连接超时，请重试 · '+retained;
    if (reason === 'EXTERNAL_METADATA_OS_TRANSPORT_UNAVAILABLE') return '系统网络组件不可用 · '+retained;
    if (/EPOCH_ECI_(MEMBER_MISSING|COLUMNS_CHANGED)/.test(reason)) return '来源数据格式已变化 · '+retained;
    if (reason === 'FX_DATE_UNUSABLE') return '来源汇率日期不可用 · '+retained;
    if (receipt?.status === 'BUSY') return '正在更新';
    return '更新失败 · '+retained;
  };
  const metricNames = {lmarena_text:'LMArena 文本 Elo（非百分制）',livebench_global:'LiveBench 综合',livebench_reasoning:'推理',livebench_coding:'编程',livebench_agentic_coding:'Agent 编程',livebench_math:'数学',livebench_data_analysis:'数据分析',livebench_language:'语言',livebench_instruction_following:'指令遵循',eci:'Epoch 能力指数（非百分制）', artificial_analysis_intelligence_index:'AA 智能指数',
    artificial_analysis_coding_index:'AA 编程指数', artificial_analysis_agentic_index:'AA 智能体指数',
    aa_lcr:'长上下文推理', ifbench:'指令遵循', gpqa_diamond:'科学推理 · GPQA',
    hle:'高难度知识推理 · HLE', critpt:'前沿科学推理 · CritPt', mmmu_pro:'多模态理解',
    aa_omniscience_non_hallucination_rate:'知识边界与幻觉控制'};
  // Reuse the existing category controller and its keyboard navigation.
  q('[data-category="external-data"]').addEventListener('click', () => {
    load().catch(() => status('来源读取失败'));
  });
  q('[data-category="literature"]').addEventListener('click', () => {
    load().catch(() => status('来源读取失败'));
  });
  function field(label, value, type = 'text') {
    const box = el('label', label, 'field-label');
    const input = el('input', null, 'field');
    input.type = type;
    input.value = value;
    input.autocomplete = 'off';
    box.append(input);
    return [box, input];
  }
  async function render(resetPreferences=false) {
    const list = q('#settings-external-sources');
    const open = new Set([...list.querySelectorAll('details[open]')].map(row => row.dataset.sourceId));
    list.replaceChildren();
    if(resetPreferences)q('#settings-literature-sources').replaceChildren();
    const groups = new Map();
    for (const [key,label] of [['scores','模型评分'],['models','模型资料与报价'],['fx','汇率'],['literature','文献来源']]) {
      const group=el('section',null,'desktop-settings-source-group');
      group.dataset.sourceGroup=key;
      if(key!=='literature')group.append(el('h4',label));
      list.append(group);
      groups.set(key,group);
    }
    for (const item of state.sources) {
      const c = item.config;
      const card = el('details', null, 'desktop-settings-source-card');
      card.dataset.sourceId = c.source_id;
      card.open = open.has(c.source_id);
      const head = el('summary');
      const title = el('span');
      title.append(el('strong', c.display_name), el('small',
        `${c.format === "ECB_FX_XML" ? (item.cached_currency_count || 0) + " 种币种" : item.cached_model_count + " 个模型"} · ${!c.enabled ? '已停用' : c.credential_env && !item.credential_configured ? '需配置凭据' : item.last_refresh?.status === 'ERROR' ? '更新失败' : item.status === 'CACHE_INVALID' ? '缓存异常，请更新' : item.status === 'CACHED' ? '已缓存' : item.status === 'STALE' ? '缓存待更新' : '—'}`));
      card.append(head);
      const body = el('div', null, 'desktop-settings-source-fields');
      const [nameBox, name] = field('来源名称', c.display_name);
      const [urlBox, url] = field('HTTPS 地址', c.endpoint, 'url');
      const [keyBox, key] = field('凭据变量', c.credential_env);
      key.placeholder = '—';
      key.spellcheck = false;
      key.title = '环境变量名，不填写 Key';
      if (c.source_id === 'exchange-rates') {
        url.readOnly=true;
        key.readOnly=true;
        keyBox.hidden=true;
        url.title='ECB 官方日汇率 · 每日检查更新';
      }
      const enabledBox = el('span', null, 'desktop-settings-source-head-actions');
      const enabled = el('button', null, 'switch');
      enabled.type = 'button';
      enabled.setAttribute('role', 'switch');
      enabled.setAttribute('aria-label', '启用此来源');
      enabled.setAttribute('aria-checked', String(c.enabled));
      let isEnabled = c.enabled;
      enabled.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        if(busy)return;
        isEnabled=!isEnabled;
        enabled.setAttribute('aria-checked',String(isEnabled));
        Memorive.settingsBridge.syncSaveState();
      });
      // Update this source now: the same ⟳ button the literature sources have, left of the switch.
      const refresh = el('button', '⟳', 'icon-button');
      refresh.type = 'button';
      refresh.disabled = !c.enabled;
      refresh.setAttribute('aria-label', `更新 ${c.display_name}`);
      refresh.title = '更新数据';
      enabledBox.append(refresh, enabled, el('span', '›', 'desktop-settings-disclosure-icon'));
      head.append(title, enabledBox);
      const dirty = () => name.value !== c.display_name || url.value.trim() !== c.endpoint || key.value.trim() !== c.credential_env || isEnabled !== c.enabled;
      card.__isDirty = dirty;
      let disabledFields = [];
      const disableFields = () => {
        disabledFields = [...list.querySelectorAll('input,select,button')].map(control => [control, control.disabled]);
        disabledFields.forEach(([control]) => { control.disabled = true; });
      };
      const restoreFields = () => {
        disabledFields.forEach(([control, disabled]) => { if (control.isConnected) control.disabled = disabled; });
        card.removeAttribute('aria-busy');
      };
      refresh.addEventListener('click', async event => {
        event.preventDefault();
        event.stopPropagation();
        if (busy) return;
        if (dirty()) {
          status('请先保存');
          return;
        }
        busy = true;
        disableFields();
        refresh.setAttribute('aria-busy','true');
        card.setAttribute('aria-busy', 'true');
        status(`正在更新 ${c.display_name}`);
        try {
          const receipt = await call('settings.external_sources_refresh', {source_id:c.source_id, expected_revision:state.revision});
          await load();
          refreshCurrent();
          status(`${c.display_name} · `+(receipt.status === 'PASS' ? '已更新' : failureCopy(receipt)));
        } catch (_) { status(`${c.display_name} · 更新失败 · 请检查网络`); }
        finally {
          busy = false;
          refresh.removeAttribute('aria-busy');
          restoreFields();
        }
      });
      card.__save=async()=>{
        const next={...c,display_name:name.value.trim(),endpoint:url.value.trim(),enabled:isEnabled};
        const changedOrigin=new URL(next.endpoint).origin!==new URL(c.endpoint).origin;
        next.credential_env=changedOrigin?'':key.value.trim();
        next.credential_origin=next.credential_env?new URL(next.endpoint).origin:'';
        if(changedOrigin)next.redistribution='LICENSE_REVIEW_REQUIRED';
        state=await call('settings.external_sources_save',{source:next,expected_revision:state.revision});
        Object.assign(c,state.sources.find(x=>x.config.source_id===c.source_id).config);
        key.value=c.credential_env;
        refresh.disabled=!c.enabled;
      };
      card.__syncAvailability=()=>{refresh.disabled=busy||!c.enabled;};
      [name,url,key].forEach(input=>{
        for(const eventName of ['input','change'])input.addEventListener(eventName,()=>Memorive.settingsBridge.syncSaveState());
      });
      refresh.textContent='⟳';
      refresh.className='icon-button';
      refresh.setAttribute('aria-label','更新 '+c.display_name);
      refresh.title='更新数据';
      urlBox.classList.add('desktop-settings-source-wide');
      keyBox.classList.add('desktop-settings-source-wide');
      enabledBox.prepend(refresh);
      body.append(nameBox, urlBox, keyBox);
      if (item.retrieved_at) {
        const time = el('small', new Date(item.retrieved_at).toLocaleString());
        time.title = '最近更新';
        body.append(time);
      }
      card.append(body);
      const groupKey=c.format==='ECB_FX_XML'?'fx':c.format==='OPENROUTER'?'models':'scores';
      groups.get(groupKey).append(card);
    }
    renderLiterature(groups.get('literature'));
    if(!q('#settings-literature-sources [data-research-preferences]'))
      await Memorive.research?.mountPreferences(q('#settings-literature-sources'));
    for (const [key,group] of groups) if(key!=='literature' && group.children.length===1) group.remove();
  }
  function renderLiterature(group) {
    const literature=state.literature;
    if(!literature)return;
    group.append(el('h4','文献来源'));
    const endpoints={
      'https://export.arxiv.org/api/query':'arxiv',
      'https://api.crossref.org/works':'crossref',
      'https://api.clarivate.com/apis/wos-starter/v1/documents':'web-of-science'
    };
    for(const slot of literature.slots) {
      const row=el('details',null,'desktop-settings-source-card');
      row.dataset.sourceId=slot.slot_id;
      const summary=el('summary'),title=el('span');
      title.append(el('strong',slot.display_name||'未配置'),el('small',
        slot.configuration_status==='CREDENTIAL_REQUIRED'?'待配置凭据':slot.enabled?'已启用':'已停用'));
      const toggle=el('button',null,'switch');
      toggle.type='button';
      toggle.setAttribute('role','switch');
      toggle.setAttribute('aria-label','启用 '+(slot.display_name||'此来源'));
      toggle.setAttribute('aria-checked',String(slot.enabled));
      const headActions=el('span',null,'desktop-settings-source-head-actions');
      const refresh=el('button','⟳','icon-button');
      refresh.type='button';
      refresh.setAttribute('aria-label','更新 '+slot.display_name);
      refresh.title='更新数据';
      let receiving=false,availabilityRevision=0;
      const syncAvailability=()=>{
        refresh.disabled=busy||!receiving||!slot.enabled||slot.configuration_status==='CREDENTIAL_REQUIRED';
        const reason=!receiving?'请先开启并保存接收外部文献':!slot.enabled?'来源已停用':slot.configuration_status==='CREDENTIAL_REQUIRED'?'请先配置凭据':'';
        refresh.setAttribute('aria-label','更新 '+slot.display_name+(reason?' · '+reason:''));
        refresh.title=refresh.getAttribute('aria-label');
      };
      row.__setReceiving=value=>{
        availabilityRevision++;
        receiving=!!value;
        syncAvailability();
      };
      row.__syncAvailability=syncAvailability;
      refresh.disabled=true;
      const requestedRevision=availabilityRevision;
      call('research.config_get').then(value=>{
        if(refresh.isConnected&&requestedRevision===availabilityRevision)row.__setReceiving(value.config.passive_enabled);
      }).catch(()=>{if(requestedRevision===availabilityRevision)refresh.disabled=true;});
      headActions.append(refresh,toggle,el('span','›','desktop-settings-disclosure-icon'));
      summary.append(title,headActions);
      row.append(summary);
      const fields=el('div',null,'desktop-settings-source-fields');
      const [nameBox,name]=field('来源名称',slot.display_name);
      const [urlBox,url]=field('HTTPS 地址',slot.endpoint,'url');
      const [keyBox,key]=field('凭据变量',slot.credential_env);
      key.placeholder=slot.provider==='web-of-science'?'WOS_API_KEY':'无需凭据';
      key.title='只填写环境变量名，不填写 Key';
      key.spellcheck=false;
      urlBox.classList.add('desktop-settings-source-wide');
      keyBox.classList.add('desktop-settings-source-wide');
      const note=el('small',slot.provider==='web-of-science'?'Starter API · Key 暂空；取得后设置对应环境变量，再启用此来源':'公开元数据来源，无需 API Key');
      let isEnabled=slot.enabled;
      const payload=()=>({slot_id:slot.slot_id,provider:endpoints[url.value.trim()]||null,
        display_name:name.value.trim(),endpoint:url.value.trim(),credential_env:key.value.trim(),enabled:isEnabled});
      row.__isDirty=()=>name.value!==slot.display_name||url.value.trim()!==slot.endpoint||key.value.trim()!==slot.credential_env||isEnabled!==slot.enabled;
      const persist=async source=>{
        state=await call('settings.external_sources_save',{source,expected_revision:state.literature.revision});
        refreshCurrent();
      };
      row.__save=async()=>{
        if(!endpoints[url.value.trim()])throw new Error('文献来源地址尚未接入');
        await persist(payload());
        Object.assign(slot,state.literature.slots.find(x=>x.slot_id===slot.slot_id));
        title.querySelector('small').textContent=slot.configuration_status==='CREDENTIAL_REQUIRED'?'待配置凭据':slot.enabled?'已启用':'已停用';
        syncAvailability();
      };
      refresh.onclick=async event=>{
        event.preventDefault();
        event.stopPropagation();
        if(busy)return;
        if(sourcesDirty() || q('[data-research-preferences]')?.__isDirty?.()){
          status('请先保存修改');
          return;
        }
        busy=true;
        refresh.disabled=true;
        refresh.setAttribute('aria-busy','true');
        try{
          const receipt=await call('research.source_refresh',{source:slot.provider,request_id:'source-'+crypto.randomUUID()});
          status(slot.display_name+' · '+(['QUEUED','RUNNING'].includes(receipt.status)?'已开始更新，可在当前任务查看进度':receipt.status==='SUCCEEDED'?'已更新':'请查看任务详情'));
          refreshCurrent();
        }catch(_){status(slot.display_name+' · 更新未完成，请检查研究方向、来源配置和网络');}
        finally{
          busy=false;
          syncAvailability();
          refresh.removeAttribute('aria-busy');
        }
      };
      toggle.onclick=event=>{
        event.preventDefault();
        event.stopPropagation();
        if(busy)return;
        isEnabled=!isEnabled;
        toggle.setAttribute('aria-checked',String(isEnabled));
        Memorive.settingsBridge.syncSaveState();
      };
      [name,url,key].forEach(input=>{
        for(const eventName of ['input','change'])input.addEventListener(eventName,()=>Memorive.settingsBridge.syncSaveState());
      });
      fields.append(nameBox,urlBox,keyBox,note);
      row.append(fields);
      group.append(row);
    }
  }
  const sourceCards=()=>[...root.querySelectorAll('#settings-external-sources .desktop-settings-source-card,#settings-literature-sources .desktop-settings-source-card')];
  window.addEventListener('desktop:research-preferences-saved',event=>{
    for(const row of sourceCards())row.__setReceiving?.(event.detail.config.passive_enabled);
  });
  const sourcesDirty=()=>sourceCards().some(card=>card.__isDirty?.());
  async function load(force=false) {
    if(!force && (sourcesDirty() || q('[data-research-preferences]')?.__isDirty?.()))return;
    const generation=++loadGeneration;
    const loaded=await call('settings.external_sources_get');
    if(generation!==loadGeneration)return;
    // A late category-load response must not erase edits made while it was in flight.
    if(!force && (sourcesDirty() || q('[data-research-preferences]')?.__isDirty?.()))return;
    state=loaded;
    await render(force);
  }
  Memorive.settingsBridge.registerSaveParticipant('external-sources',{
    isDirty:sourcesDirty,
    afterUnlock:()=>sourceCards().forEach(card=>card.__syncAvailability?.()),
    save:async()=>{
      for(const card of sourceCards())if(card.__isDirty?.())await card.__save();
      refreshCurrent();
    },
    discard:()=>load(true)
  });
  const nodeMap = {ingest:'ingest',embedding:'chunk_embedding',card:'card_distill','card-review':'transport_review',context:'context_pack',analysis:'analysis','judgment-review':'judgment_review'};
  let currentNode = null;
  const refreshCurrent = () => {
    window.dispatchEvent(new CustomEvent('desktop:external-sources-changed', {detail:{revision:state?.revision}}));
    if (currentNode?.isConnected) refreshNode(currentNode);
  };
  async function refreshNode(node) {
    const request = ++sequence, nodeId = nodeMap[node.dataset.step];
    const profileRef = node.dataset.profileRef || null;
    q('#settings-reference-summary').textContent = '—';
    q('#settings-reference-details').replaceChildren();
    Memorive.examPanel?.clearReferences();
    if (!nodeId) return;
    try {
      const result = await call('settings.external_model_reference', {node_id:nodeId, preview_profile_ref:profileRef});
      if (request !== sequence || node !== currentNode || profileRef !== (node.dataset.profileRef || null)) return;
      if (result.cached_exam_score_resolved) {
        const score = result.internal_exam_score;
        node.dataset.testScore = Number.isInteger(score) ? String(score) : '';
        q('#settings-node-test-score').textContent = node.dataset.testScore || '—';
        const badge = node.querySelector('.desktop-settings-node-test-score');
        if (badge) badge.textContent = node.dataset.testScore || '—';
      }
      window.dispatchEvent(new CustomEvent('desktop:external-model-reference', {detail:result}));
      q('#settings-reference-summary').textContent = result.references.length ? '↗' : '—';
      const detail = q('#settings-reference-details');
      for (const row of result.references) {
        const section = el('section');
        section.append(el('strong', `${row.source_name || row.source_id} · ${row.stale ? '缓存待更新' : '已缓存'}`));
        if (row.local_base_model_reference) section.append(el('small', '基础模型参考 · 量化版本未实测'));
        if (row.benchmark_effort) section.append(el('small', `来源思考档：${row.benchmark_effort}${row.effort_mismatch ? ' · 与当前档位不同' : ''}`));
        const relevant = row.role_relevant_metrics || [];
        for (const [metric, value] of Object.entries(row.display_metrics || row.metrics)) {
          const n = value && typeof value === 'object' ? value.value : value;
          section.append(el('p', `${metricNames[metric] || metric}${relevant.includes(metric) ? ' · 相关' : ''}：${n == null ? '—' : typeof n==='number' ? Number(n.toFixed(2)) : n}`));
        }
        if (row.confidence_interval) section.append(el('small', `来源置信区间：${row.confidence_interval.join('—')}`));
        if (row.operational?.context_length) section.append(el('p', `上下文：${row.operational.context_length} tokens`));
        section.append(el('small', `${row.version || '—'} · ${row.retrieved_at ? new Date(row.retrieved_at).toLocaleDateString() : '—'}`));
        detail.append(section);
      }
    } catch (_) { if (request === sequence) q('#settings-reference-summary').textContent = '—'; }
  }
  root.addEventListener('click', event => {
    const node = event.target.closest('button.desktop-settings-node[data-step]');
    if (node) {
      currentNode = node;
      refreshNode(node);
    }
  });
  window.addEventListener('desktop:workflow-model-selected', event => {
    currentNode = event.detail.node;
    refreshCurrent();
  });
  window.addEventListener('desktop:workflow-config-updated', () => queueMicrotask(refreshCurrent));
  Memorive.externalSources = Object.freeze({load, render, refreshCurrent});
})();

