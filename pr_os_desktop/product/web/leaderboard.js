/* Product leaderboard. All data comes from the allowlisted desktop bridge. */
(() => {
  'use strict';
  const root = document.getElementById('p08-partleaderboard-preview');
  if (!root || root.dataset.leaderboardMounted) return;
  root.dataset.leaderboardMounted = 'true';
  const q = s => root.querySelector(s);
  const qa = s => [...root.querySelectorAll(s)];
  const tableWrap = q('.lb-table-wrap'), tableHead = tableWrap.querySelector('thead');
  // Native scrollbars paint above sticky content; reserve the header's height
  // in the vertical track instead of trying to outrank it with z-index.
  const headerObserver = new ResizeObserver(() => {
    if (tableHead.offsetHeight > 0)
      tableWrap.style.setProperty('--lb-table-header-height', tableHead.offsetHeight + 'px');
  });
  headerObserver.observe(tableHead);
  window.addEventListener('beforeunload', () => headerObserver.disconnect(), {once:true});

  const C = window.P08LeaderboardCurrency;
  const shell = window.P08Preview_partleaderboard;
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined && text !== null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const iconPaths = {
    star:'m12 3 2.78 5.63L21 9.55l-4.5 4.39 1.06 6.19L12 17.2l-5.56 2.93 1.06-6.19L3 9.55l6.22-.92Z',
    'sliders-horizontal':'M3 6h6m4 0h8M3 12h12m4 0h2M3 18h3m4 0h11M9 3v6m6 0v6M6 15v6',
    save:'M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2ZM7 3v6h10V3M7 21v-8h10v8',
    x:'M6 6l12 12M6 18 18 6', check:'m5 12 4 4L19 6',
    'rotate-ccw':'M3 4v6h6M4 10a8 8 0 1 1 1 8',
    back:'m14 6-6 6 6 6', plus:'M12 5v14M5 12h14', down:'m6 9 6 6 6-6',
    help:'M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2-3 4M12 17h.01'
  };
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'lb-symbol');
    svg.setAttribute('aria-hidden', 'true');
    if (name === 'help') {
      const circle = document.createElementNS(svg.namespaceURI, 'circle');
      circle.setAttribute('cx','12');circle.setAttribute('cy','12');circle.setAttribute('r','10');svg.append(circle);
    }
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', iconPaths[name] || iconPaths.down); svg.append(path);
    return svg;
  }
  qa('[data-lb-icon]').forEach(n => {
    if (n.dataset.lbIcon === 'refresh-cw') {
      const b = n.parentElement; b.classList.add('icon-button'); b.textContent = '↻';
      window.__P08_UI_CONTROLS__?.icons(b); // Reuse the current app's refresh asset.
    } else n.replaceWith(icon(n.dataset.lbIcon));
  });
  q('#lb-rule-toggle').setAttribute('aria-controls', 'p08-right-panel');
  let data = null, sequence = 0, pendingSave = Promise.resolve(), pollTimer = 0, pollStep = 0;
  let draft = null, pane = null, selected = null, owner = null, menuOwner = null, saving = false;
  const defaultOrder = Object.freeze({sort:'ability', direction:'desc'});
  let starredOnly = false, sort = defaultOrder.sort, direction = defaultOrder.direction, filters = {};
  let fxPending = null, fxAttempted = false, fxError = '';
  const moneyKeys = new Set(['input','output','mixed']);
  const labels = {"rank": "序号", "model": "模型", "vendor": "供应商", "region": "归属", "ability": "能力指数", "input": "输入 / M", "output": "输出 / M", "mixed": "混合 / M", "value": "性价比"};
  const fullCols = Object.keys(labels), compactCols = ['model','vendor','ability','pricing','value'];
  labels.pricing='输入 / 输出';
  const codeLabel = code => code === 'CNY' ? 'RMB' : code;
  const now = () => new Date().toISOString();
  const usableFx = () => ['available','cached'].includes(C.snapshotStatus(data?.fx, now()));
  const displayCode = () => data?.preferences.currency || 'USD';
  const effortLabel = effort => effort === 'reference' ? uiText('模型级参考') : effort || uiText('档位未标注');
  // Largest-remainder rounding makes the displayed shares sum to exactly 100.
  const percentShares = values => {
    const safe=values.map(v=>Number.isFinite(v)&&v>0?v:0),sum=safe.reduce((a,b)=>a+b,0);
    if(!sum)return safe.map(()=>0);
    const raw=safe.map(v=>100*v/sum),result=raw.map(Math.floor);
    const order=raw.map((v,i)=>({i,f:v-result[i]})).sort((a,b)=>b.f-a.f||a.i-b.i);
    for(let i=0,n=100-result.reduce((a,b)=>a+b,0);i<n;i++)result[order[i].i]++;
    return result;
  };
  const scoreText = v => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(1) : '—';
  const notify = text => {
    q('#lb-status').textContent = text;
    q('#lb-context').textContent = text;
  };
  function uiText(text) { return window.__P08_INTEGRATED_APP__?.translate?.(text) || text; }
  function emptyCatalogText() {
    const state = data?.refresh?.status;
    return state === 'RUNNING' ? uiText(uiText('正在获取模型数据…')) :
      state === 'NO_ENABLED_SOURCES' ? uiText(uiText('模型数据来源已停用')) :
      state === 'ERROR' ? uiText(uiText('模型数据获取失败 · 点击刷新重试')) :
      uiText(uiText('尚无模型数据 · 点击刷新获取'));
  }
  const call = async (method, params = {}) => {
    if (!['settings.leaderboard_get','settings.leaderboard_save','settings.leaderboard_refresh','settings.external_sources_refresh'].includes(method)) throw new Error('METHOD_NOT_ALLOWED');
    if (typeof window.pywebview?.api?.call === 'function') return (window.P08Decorations?.trackCall(method, () => window.pywebview.api.call(method, params)) || window.pywebview.api.call(method, params));
    throw new Error('DESKTOP_BRIDGE_UNAVAILABLE');
  };
  function accept(value) {
    if (value?.schema_version !== 'ModelLeaderboardProjection-v4' || !Array.isArray(value.rows) ||
        !value.preferences || !Array.isArray(value.sources) || value.qualification_eligible !== false) throw new Error('PROJECTION_INVALID');
    const unchanged = data?.projection_key && data.projection_key === value.projection_key;
    const previousCurrency = data?.preferences.currency;
    const sourcesChanged = JSON.stringify(data?.sources) !== JSON.stringify(value.sources);
    data = value;
    if (previousCurrency !== data.preferences.currency) window.dispatchEvent(new CustomEvent('p08:display-currency-changed', {
      detail:{currency:data.preferences.currency,display_label:codeLabel(data.preferences.currency),source:'leaderboard'}
    }));
    if (unchanged) { updateMeta(); return; }
    render();
    if (pane === 'model') renderDetail();
    if (pane === 'preferences' && sourcesChanged) {
      draft = reconcileDraft(draft);renderPreferences();
    }
  }
  async function load() {
    const request = ++sequence;
    try {
      const result = await call('settings.leaderboard_get');
      if (request === sequence) accept(result);
      if(document.body.dataset.p08ActiveRoute==='leaderboard'&&data?.preferences.currency!=='USD'&&!usableFx()&&!fxAttempted&&data.refresh.status!=='RUNNING')void ensureFx();
      return true;
    } catch (_) { if (request === sequence) notify(data ? uiText('读取失败 · 已保留当前列表') : uiText('数据暂不可用 · 点击刷新重试')); return false; }
  }
  function save(changes) {
    const work = async () => {
      if (!data) throw new Error('DATA_UNAVAILABLE');
      ++sequence;
      const patch = typeof changes === 'function' ? changes(data.preferences) : changes;
      try {
        const result = await call('settings.leaderboard_save', {expected_revision:data.revision, changes:patch});
        accept(result); return true;
      } catch (_) {
        // Refresh revision after conflict; never replay a stale draft as if it saved.
        await load(); notify(uiText('未保存 · 数据可能已在其他页面更新，请重试')); return false;
      }
    };
    const result = pendingSave.then(work, work);
    pendingSave = result.catch(() => false);
    return result;
  }
  async function ensureFx(force=false) {
    if(fxPending)return fxPending;
    if(!data||data.preferences.currency==='USD'||usableFx()||data.refresh.status==='RUNNING'||(!force&&fxAttempted))return;
    fxAttempted=true;fxError='';
    const source=data.sources.find(s=>s.role==='FX');
    if(!source?.enabled){fxError=uiText('汇率来源已停用，请在设置中开启');updateMeta();return;}
    const work=async()=>{
      try{
        const receipt=await call('settings.external_sources_refresh',{source_id:source.id,expected_revision:data.source_revision});
        if(receipt.status!=='PASS')throw new Error('FX_REFRESH_FAILED');
        await load();if(!usableFx())throw new Error('FX_UNAVAILABLE');
        window.dispatchEvent(new CustomEvent('p08:model-commerce-context-changed'));
      }catch(_){fxError=uiText('汇率更新失败 · 请检查网络后点击刷新');}
      finally{fxPending=null;render();if(pane==='model')renderDetail();}
    };
    fxPending=Promise.resolve().then(work);updateMeta();return fxPending;
  }
  const modelName = m => (m.name || m.base_model) + ' · ' + effortLabel(m.effort);
  function amount(m, key) {
    if (key === 'mixed') return m.mixed_usd;
    return m.price?.[key] ?? null;
  }
  function price(m, key) {
    if(amount(m,key)==null && m.price_status==='NO_CURRENT_PUBLIC_ROUTE')return uiText('暂无渠道价');
    const converted = C.convert(amount(m,key), 'USD', displayCode(), data.fx, now());
    return C.format(converted.amount, displayCode(), false);
  }
  function quoteTip(m) {
    const quote=m.price||m.channel_price;
    if(!quote&&m.price_status==='NO_CURRENT_PUBLIC_ROUTE')return uiText('已检查 OpenRouter、models.dev 与 LiteLLM 当前公开目录，未找到与该模型身份精确绑定的当前 API 渠道价；保留能力记录，但不参与价格与性价比计算。');
    if(!quote)return uiText('当前来源暂无有效报价');
    return (quote.source_name || quote.source_id || uiText('外部来源'))+' · '+(quote.retrieved_at || uiText('更新时间未提供'))+
      (m.price?uiText('\n常规价已核实，已排除渠道优惠。'):uiText('\n常规价待核实，当前渠道价不参与价格比较。'))+
      uiText('\n保持同一模型与渠道口径；长上下文、缓存及附加费用另计。')+
      uiText('\n用于本榜单的价格比较；不代表已配置 API、CLI 或实际账单。');
  }
  function valueFor(m, key) {
    if (moneyKeys.has(key)) return amount(m,key);
    if (key === 'model') return m.name + ' ' + modelName(m);
    return m[key];
  }
  function filtered() {
    return data.rows.filter(m => {
      if (starredOnly ? !m.starred : !(m.catalog_visible ?? m.catalog_eligible)) return false;
      return matchesFilters(m);
    }).sort((a,b) => {
      let x = valueFor(a,sort), y = valueFor(b,sort);
      if (x === null || x === undefined) return y === null || y === undefined ? a.id.localeCompare(b.id) : 1;
      if (y === null || y === undefined) return -1;
      if (['ability','value'].includes(sort)) { x = Number(x.toFixed(1)); y = Number(y.toFixed(1)); }
      const comparison = typeof x === 'number' ? x-y : String(x).localeCompare(String(y),'zh-CN');
      return comparison ? comparison * (direction === 'asc' ? 1 : -1) : a.id.localeCompare(b.id);
    });
  }
  function matchesFilters(m,except=null) {
      return Object.entries(filters).every(([key, f]) => {
        if(key===except)return true;
        const v = valueFor(m,key);
        if (key==='model') return (!f.query||String(v||'').toLocaleLowerCase().includes(f.query.toLocaleLowerCase()))&&(!f.effort||String(m.effort||'unknown')===f.effort);
        if (f.choices) return f.choices.includes(v);
        return v !== null && v !== undefined && C.inRange(v, f.min, f.max);
      });
  }
  function formulaTip() {
    const a = data.algorithm;
    const total = a.ability_weight + a.cost_weight;
    const alpha = a.ability_weight / total, beta = a.cost_weight / total;
    return uiText('A = 各来源内部的百分位加权值（0–100）；每个来源至少 10 个样本。\n') +
      uiText('缺少某来源时，仅对已有分数重新分配权重；详情显示来源覆盖率。\n') +
      'C = ' + a.input_ratio + uiText(' × 输入单价 + ') + Number((1-a.input_ratio).toFixed(4)) + uiText(' × 输出单价（USD / M）。\n') +
      uiText('C₀ = 完整价格样本的中位数：') + (a.reference_cost_usd === null ? uiText('暂无') : a.reference_cost_usd.toPrecision(5)) + ' USD / M。\n' +
      'K = 100 / (1 + C / C₀)。\n' +
      (alpha === 0 ? 'V = K。' : beta === 0 ? 'V = A。' : 'V = 100 × (A/100)^' + alpha.toFixed(3) + ' × (K/100)^' + beta.toFixed(3) + '。') +
      uiText('\n缺失数据不按 0 分；零价格不参与成本评分。星标、筛选和币种不改变计算样本。') +
      uiText('\n只比较 Token 单价，不预测实际用量。未标档位的 Epoch 分数单列为“模型级参考”，不用于其他档位。');
  }
  function updateTip() {
    const stamp = data.updated_at ? data.updated_at.slice(0,10).replaceAll('-','/') : '—';
    const t = q('#lb-updated-date'); t.textContent = stamp;
    if (data.updated_at) t.setAttribute('datetime', data.updated_at); else t.removeAttribute('datetime');
    const fx = data.fx;
    const rates = usableFx() ? '1 USD = ' + fx.unitsPerUsd.CNY.toFixed(4) + ' RMB = ' + fx.unitsPerUsd.JPY.toFixed(4) + ' JPY' : fxPending?uiText('正在更新汇率…'):uiText('汇率暂不可用；USD 可查看原始报价');
    const details = data.sources.map(s => {
      const limited=s.last_refresh?.status==='RATE_LIMITED';
      const recovery=limited&&s.last_refresh?.retry_after?uiText(' · 预计恢复 ')+new Date(s.last_refresh.retry_after).toLocaleString():'';
      return s.name + '：' + (s.retrieved_at ? s.retrieved_at.slice(0,10) : uiText('未更新')) +
        (s.status === 'CACHED' ? '' : ' · ' + ({DISABLED:uiText('已停用'),STALE:uiText('待更新'),NOT_FETCHED:uiText('尚无数据'),CACHE_INVALID:uiText('缓存异常')}[s.status] || uiText('未知状态'))) +
        (limited?uiText(' · 额度受限，继续使用旧缓存'):'') + recovery;
    });
    q('#lb-updated').dataset.p08Tip = uiText('上次更新：') + stamp + uiText('（已启用模型来源中最早的缓存时间）\n') +
      rates + uiText('\n汇率日期：') + (fx?.rateDate || '—') + (fx ? ' · ' + fx.source : '') + '\n' + details.join('\n');
  }
  function render() {
    const rows = filtered(), head = q('#lb-headers'), body = q('#lb-rows');
    const cols = pane ? compactCols : fullCols;
    root.dataset.lbCompact=String(!!pane);
    if(head.dataset.columns!==cols.join('|')) {
      head.replaceChildren();q('#lb-column-widths').replaceChildren();
      const widths=pane?[30,14,16,23,17]:[4,21,11,7,11,11,11,11,13];
      cols.forEach((key,index)=>{
        const col=el('col');col.style.width=widths[index]+'%';q('#lb-column-widths').append(col);
        const th=el('th');th.dataset.col=key;
        if(key==='rank'){th.textContent=labels[key];head.append(th);return;}
        const group=el('div',null,'lb-th-group');
        const addHeader=(field,label)=>{
          const b=el('button',null,'lb-th-btn');b.type='button';b.dataset.header=field;
          const caption=el('span',null,'lb-th-caption');caption.append(el('span',label,'lb-th-label'),icon('down'));
          b.append(caption);b.setAttribute('aria-haspopup','dialog');
          b.setAttribute('aria-controls','lb-column-menu');group.append(b);
        };
        if(key==='pricing'){addHeader('input',uiText('输入'));group.append(el('span','/','lb-price-divider'));addHeader('output',uiText('输出'));}
        else addHeader(key,labels[key]);
        if(key==='value'){
          const help=el('button',null,'lb-icon-btn lb-help');help.id='lb-value-help';help.type='button';
          help.setAttribute('aria-label',uiText('性价比计算规则'));help.append(icon('help'));group.append(help);
        }
        th.append(group);head.append(th);
      });
      head.dataset.columns=cols.join('|');
    }
    qa('#lb-headers [data-header]').forEach(b=>{
      b.dataset.filtered=String(!!filters[b.dataset.header]);
      b.setAttribute('aria-expanded',String(menuOwner?.dataset.header===b.dataset.header&&!q('#lb-column-menu').hidden));
      b.dataset.direction=b.dataset.header===sort?direction:'';
      b.setAttribute('aria-label',uiText(labels[b.dataset.header])+uiText('，排序与筛选'));
    });
    qa('#lb-headers th').forEach(th=>th.setAttribute('aria-sort',sort===th.dataset.col?
      direction==='asc'?'ascending':'descending':'none'));
    q('#lb-value-help').dataset.p08Tip=formulaTip();
    const fragment = document.createDocumentFragment();
    rows.forEach((m,i) => {
      const tr=el('tr');tr.dataset.model=m.id;tr.setAttribute('aria-selected',String(selected===m.id && pane==='model'));
      tr.classList.toggle('lb-selected',selected===m.id&&pane==='model');
      tr.addEventListener('click',e=>{
        if(e.target.closest('button,a,input,select'))return;
        openModel(m.id,tr.querySelector('[data-open]'));
      });
      cols.forEach(key => {
        const td=el('td');td.dataset.col=key;
        if(key==='rank') td.textContent=String(i+1);
        else if(key==='model') {
          const wrap=el('div',null,'lb-model-cell');
          const star=el('button',null,'lb-icon-btn lb-icon-compact lb-star');star.type='button';star.dataset.star=m.id;
          star.setAttribute('aria-pressed',String(m.starred));star.setAttribute('aria-label',(m.starred?uiText('取消星标 '):uiText('星标 '))+modelName(m));
          star.append(icon('star'));star.addEventListener('click',() => {
            void save(p => ({stars:p.stars.includes(m.id)?p.stars.filter(id=>id!==m.id):[...p.stars,m.id]}));
          });
          const name=el('button',null,'lb-model-open');name.type='button';name.dataset.open=m.id;
          name.append(el('strong',m.name || m.base_model),el('small',
            m.official_release&&!m.catalog_eligible?uiText('官方新发布 · 暂无基准评分'):effortLabel(m.effort)));
          name.title=modelName(m);
          name.addEventListener('click',()=>openModel(m.id,name));wrap.append(star,name);td.append(wrap);
        } else if(key==='pricing') {
          const pair=el('span',null,'lb-price-pair');
          pair.append(el('span',price(m,'input')),el('span','/','lb-price-divider'),el('span',price(m,'output')));
          td.append(pair);td.title=codeLabel(displayCode())+uiText(' / M · 输入 ')+price(m,'input')+uiText(' / 输出 ')+price(m,'output');
        } else {td.textContent=moneyKeys.has(key)?price(m,key):key==='value'&&m.price_status==='NO_CURRENT_PUBLIC_ROUTE'?uiText('不参与'):['ability','value'].includes(key)?scoreText(m[key]):(key==='region'?uiText(m[key]):m[key])||'—';if(key==='vendor')td.title=m.vendor;}
        if(moneyKeys.has(key)||key==='pricing')td.title=quoteTip(m);
        if(key==='ability'&&m.source_coverage)td.title=uiText('评分来源覆盖 ')+m.source_coverage.used+' / '+m.source_coverage.selected+uiText('；缺失来源不按零分。');
        tr.append(td);
      });fragment.append(tr);
    });
    if(!rows.length) {
      const tr=el('tr'),td=el('td',!data.rows.length?emptyCatalogText():starredOnly?uiText('当前来源中没有符合条件的星标模型'):uiText('没有符合条件的模型'),'lb-empty');
      td.colSpan=cols.length;tr.append(td);fragment.append(tr);
    }
    body.replaceChildren(fragment);
    updateMeta();
  }
  function updateMeta() {
    q('#lb-starred-only').setAttribute('aria-pressed',String(starredOnly));
    q('#lb-starred-only').dataset.p08Tip=uiText('仅看星标 · ')+data.preferences.stars.length;
    const codes=['USD','CNY','JPY'],code=data.preferences.currency,next=codes[(codes.indexOf(code)+1)%3];
    q('#lb-currency').textContent=codeLabel(code);
    q('#lb-currency').setAttribute('aria-label',uiText('当前 ')+codeLabel(code)+uiText('，切换为 ')+codeLabel(next));
    q('#lb-currency').dataset.p08Tip=uiText('当前 ')+codeLabel(code)+uiText('，点击切换为 ')+codeLabel(next)+(code!=='USD'&&!usableFx()?uiText(' · 等待有效汇率'):'');
    q('#lb-currency').setAttribute('aria-busy',String(!!fxPending));
    const quoteSources=[...new Set(data.rows.map(m=>m.price||m.channel_price).filter(Boolean).map(p=>p.source_name||p.source_id))].join(' / ');
    q('#lb-price-caption').textContent=(quoteSources?quoteSources+' · ':'')+codeLabel(displayCode())+uiText(' / 百万 Token · 输入 / 输出 ')+Math.round(data.preferences.input_ratio*100)+' : '+Math.round((1-data.preferences.input_ratio)*100);
    q('#lb-clear-filters').hidden=!Object.keys(filters).length;
    const status = data.refresh.status;
    const limited=(data.refresh.results||[]).find(x=>x.status==='RATE_LIMITED');
    q('#lb-refresh').disabled=status==='RUNNING'||status==='NO_ENABLED_SOURCES';q('#lb-refresh').setAttribute('aria-busy',String(status==='RUNNING'));
    q('#lb-context').textContent=fxPending?uiText('正在更新汇率…'):code!=='USD'&&!usableFx()?(fxError||uiText('汇率暂不可用 · 点击刷新')):status==='RUNNING'?uiText('正在刷新…'):status==='PARTIAL'?(limited?.retry_after?uiText('部分来源额度受限，已保留缓存 · ')+new Date(limited.retry_after).toLocaleString()+uiText(' 后恢复'):uiText('部分来源未更新，已保留可用缓存')):status==='ERROR'?(data.rows.length?uiText('刷新失败，已保留可用缓存'):uiText('刷新失败，暂无可用缓存')):
      data.rows.length===0?emptyCatalogText():!data.catalog_policy.default_count?uiText('所选来源暂无可评分数据 · 可刷新或调整评分偏好'):data.algorithm.cohort_size<10?uiText('来源评分样本不足 10 个，暂不计算能力指数'):data.missing_star_count&&starredOnly?data.missing_star_count+uiText(' 个星标暂未出现在当前来源中'):'';
    if (data.rows.length && status === 'PASS' && data.sources.some(s=>s.enabled&&s.status==='STALE'))
      q('#lb-context').textContent=uiText(uiText('正在使用缓存数据 · 可刷新更新'));
    q('.lb-meta').hidden=!q('#lb-context').textContent&&q('#lb-clear-filters').hidden;
    updateTip();
  }
  q('#lb-headers').addEventListener('click',e=>{
    const b=e.target.closest('[data-header]');if(!b)return;
    e.preventDefault();e.stopPropagation();openMenu(b.dataset.header,b);
  });
  q('#lb-column-menu').addEventListener('click',e=>e.stopPropagation());
  function closeMenu(restore=true) {
    const menu=q('#lb-column-menu');menu.hidden=true;menu.inert=true;
    menuOwner?.setAttribute('aria-expanded','false');
    if(restore) q('[data-header="'+menuOwner?.dataset.header+'"]')?.focus();
    menuOwner=null;
  }
  function openMenu(key,button) {
    closeMenu(false);hideTip();menuOwner=button;button.setAttribute('aria-expanded','true');
    const menu=q('#lb-column-menu'),heading=el('div',null,'lb-menu-heading'),close=el('button',null,'lb-icon-btn lb-icon-compact');
    close.type='button';close.setAttribute('aria-label',uiText('关闭筛选'));close.append(icon('x'));close.addEventListener('click',()=>closeMenu());
    heading.append(el('strong',labels[key]),close);menu.replaceChildren(heading);
    menu.hidden=false;menu.inert=false;menu.style.left='8px';menu.style.top='8px';
    const sorts=el('div',null,'lb-menu-sorts');
    ['asc','desc'].forEach(dir => {
      const b=el('button',null,'lb-menu-sort');b.type='button';b.dataset.direction=dir;
      b.append(icon('down'),el('span',dir==='asc'?uiText('升序'):uiText('降序')));b.setAttribute('aria-pressed',String(sort===key&&direction===dir));
      b.addEventListener('click',()=>{sort=key;direction=dir;closeMenu();render();q('[data-header="'+key+'"]')?.focus();});sorts.append(b);
    });
    menu.append(sorts);
    const f=filters[key]||{},form=el('form'), fields=[];
    const candidates=data.rows.filter(m=>(starredOnly?m.starred:m.catalog_eligible)&&matchesFilters(m,key));
    if(key==='model') {
      const input=el('input');input.type='search';input.value=f.query||'';input.placeholder=uiText('搜索模型名称');input.setAttribute('aria-label',uiText('搜索模型名称'));form.append(input);fields.push(input);
      const label=el('label',uiText('思考档位'),'lb-menu-field'),select=el('select');select.setAttribute('aria-label',uiText('思考档位'));
      const all=el('option',uiText('全部档位'));all.value='';select.append(all);
      [...new Set(candidates.map(m=>m.effort||'unknown'))].sort().forEach(effort=>{const o=el('option',effort==='unknown'?uiText('档位未标注'):effortLabel(effort));o.value=effort;select.append(o);});
      select.value=f.effort||'';label.append(select);form.append(label);fields.push(select);
    } else if(['vendor','region'].includes(key)) {
      const counts=new Map();candidates.forEach(m=>counts.set(m[key],(counts.get(m[key])||0)+1));
      const values=[...new Set([...counts.keys(),...(f.choices||[])])].sort(),list=el('div',null,'lb-menu-checklist');
      const search=el('input');search.type='search';search.placeholder=uiText('搜索')+uiText(labels[key]);search.setAttribute('aria-label',search.placeholder);form.append(search);
      const bulk=el('div',null,'lb-menu-bulk');
      [[uiText('全选'),true],[uiText('清空'),false]].forEach(([title,on])=>{const b=el('button',title,'lb-link-btn');b.type='button';b.addEventListener('click',()=>fields.filter(n=>!n.parentElement.hidden).forEach(n=>n.checked=on));bulk.append(b);});form.append(bulk);
      values.forEach(value=>{
        const label=el('label',null,'lb-check'),input=el('input');input.type='checkbox';input.value=value;input.checked=!f.choices||f.choices.includes(value);
        label.append(input,el('span',value),el('small',String(counts.get(value)||0),'lb-small'));list.append(label);fields.push(input);
      });form.append(list);
      search.addEventListener('input',()=>fields.forEach(n=>n.parentElement.hidden=!n.value.toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())));
    } else {
      const currency=moneyKeys.has(key)?displayCode():null;
      if(currency) form.append(el('p',codeLabel(currency)+' / M','lb-small'));
      const range=el('div',null,'lb-menu-range');
      ['min','max'].forEach((bound,i)=>{
        const input=el('input');input.type='number';input.min='0';input.step='any';input.placeholder=i?uiText('最大值'):uiText('最小值');input.setAttribute('aria-label',input.placeholder);
        input.value=currency?C.rangeInput(f[bound],currency,data.fx,now()):(f[bound]??'');input.dataset.original=input.value;fields.push(input);
        const label=el('label',i?uiText('不高于'):uiText('不低于'),'lb-menu-field');label.append(input);range.append(label);
      });form.append(range);form.dataset.currency=currency||'';
    }
    const error=el('p',null,'lb-error');error.setAttribute('role','alert');form.append(error);
    const actions=el('div',null,'lb-menu-actions'),clear=el('button',uiText('恢复默认'),'lb-link-btn'),apply=el('button',uiText('应用'),'lb-primary lb-btn');
    clear.type='button';clear.title=uiText('恢复本列筛选与默认排序（能力指数降序）');
    clear.addEventListener('click',()=>{delete filters[key];sort=defaultOrder.sort;direction=defaultOrder.direction;closeMenu();render();q('[data-header="'+key+'"]')?.focus();});
    apply.type='submit';actions.append(clear,apply);form.append(actions);
    if(moneyKeys.has(key)&&displayCode()!=='USD'&&!usableFx()){
      fields.forEach(n=>n.disabled=true);apply.disabled=true;
      error.textContent=uiText('金额筛选需要有效汇率，请先更新汇率或切回 USD');
    }
    form.addEventListener('submit',e=>{
      e.preventDefault();
      try {
        if(key==='model') { const query=fields[0].value.trim(),effort=fields[1].value;if(query||effort) filters[key]={query,effort};else delete filters[key]; }
        else if(['vendor','region'].includes(key)) {
          const choices=fields.filter(x=>x.checked).map(x=>x.value);
          if(choices.length===fields.length) delete filters[key];else filters[key]={choices};
        } else {
          const bounds=fields.map((input,i)=>{
            const bound=i?'max':'min',value=input.value;
            if(form.dataset.currency) return C.rangeBound(value,f[bound],input.dataset.original,form.dataset.currency,data.fx,now());
            return value===''?null:Number(value);
          });
          if(bounds.some(v=>v!==null&&(!Number.isFinite(v)||v<0))||(bounds.every(v=>v!==null)&&bounds[0]>bounds[1])) throw new Error('RANGE');
          if(bounds.every(v=>v===null)) delete filters[key];else filters[key]={min:bounds[0],max:bounds[1]};
        }
        closeMenu();render();
      } catch (_) {error.textContent=uiText('请输入有效范围，最小值不能超过最大值');}
    });
    menu.append(form);
    const rect=button.getBoundingClientRect(),box=menu.getBoundingClientRect();
    menu.style.left=Math.max(8,Math.min(rect.left,innerWidth-box.width-8))+'px';
    menu.style.top=Math.max(8,Math.min(rect.bottom+6,innerHeight-box.height-8))+'px';
    (form.querySelector('input[type=search]')||fields[0])?.focus({preventScroll:true});
  }
  function changePane(kind) {
    pane=kind;closeMenu(false);hideTip();
    q('#lb-rules').hidden=kind!=='preferences';q('#lb-rules').inert=kind!=='preferences';
    q('#lb-model-detail').hidden=kind!=='model';q('#lb-model-detail').inert=kind!=='model';
    q('#lb-pref-apply').hidden=kind!=='preferences';q('#lb-reset-weights').hidden=kind!=='preferences';
    q('#lb-inspector-title').textContent=kind==='preferences'?uiText('评分偏好'):uiText('模型详情');
    q('#lb-rule-toggle').setAttribute('aria-expanded',String(kind==='preferences'));
    if(kind) shell?.setInspector(true); else shell?.setInspector(false);
    q('#p08-right-panel').inert=!kind;
    if(data) render();
  }
  function openModel(id,from) {
    selected=id;owner=from;changePane('model');renderDetail();q('#lb-inspector-close').focus({preventScroll:true});
  }
  function closePane() {
    draft=null;selected=null;q('#lb-pref-dirty').hidden=true;changePane(null);
    if(owner?.dataset.open) q('[data-open="'+owner.dataset.open+'"]')?.focus({preventScroll:true});
    else q('#lb-rule-toggle').focus({preventScroll:true});
  }
  function renderDetail() {
    const panel=q('#lb-model-detail'),m=data.rows.find(x=>x.id===selected),openIds=new Set(
      [...panel.querySelectorAll('details[open][data-source]')].map(x=>x.dataset.source));
    panel.replaceChildren();
    if(!m){panel.append(el('p',uiText('该配置暂未出现在当前来源中，已有星标仍保留。')));return;}
    panel.append(el('h3',modelName(m),'lb-detail-model'));
    const product=m.product||{},a=data.algorithm;
    if(product.description) panel.append(el('p',product.description,'lb-detail-desc'));
    const section=(title)=>{const n=el('section',null,'lb-detail-section');n.append(el('h3',title));panel.append(n);return n;};
    const specs=(target,entries)=>{const dl=el('dl',null,'lb-specs');entries.forEach(([k,v])=>dl.append(el('dt',k),el('dd',String(v??uiText('来源未提供')))));target.append(dl);};
    const info=section(uiText('产品资料'));
    const modes=values=>values?.map(x=>({text:uiText('文本'),image:uiText('图像'),audio:uiText('音频'),video:uiText('视频'),file:uiText('文件')}[x]||x)).join('、')||null;
    const count=v=>typeof v==='number'?v.toLocaleString()+' Token':null;
    specs(info,[[uiText('供应商'),m.vendor],[uiText('归属'),m.region],[uiText('模型版本'),m.version],[uiText('思考档位'),effortLabel(m.effort)],
      [uiText('上下文窗口'),count(product.context_length)],[uiText('最大输出'),count(product.max_output_tokens)],
      [uiText('输入类型'),modes(product.input_modalities)],[uiText('输出类型'),modes(product.output_modalities)]]);
    if(m.official_release)specs(info,[[uiText('官方发布'),m.official_release.released_on],
      [uiText('官方资料'),m.official_release.source_name],[uiText('基准状态'),m.catalog_eligible?uiText('已有同口径评分'):uiText('等待独立基准数据')]]);
    const caps=el('div',null,'lb-capabilities');
    [['tools',uiText('工具调用')],['structured_output',uiText('结构化输出')],['reasoning',uiText('推理参数')]].forEach(([key,label])=>{
      const value=product.capabilities?.[key];
      if(value===true)caps.append(el('span',label,'lb-capability'));
    });if(caps.childElementCount)info.append(caps);
    const summary=section(uiText('参考评分')),cards=el('div',null,'lb-detail-scores');
    [[uiText('能力指数'),m.ability],[uiText('性价比'),m.value],[uiText('成本指数'),m.economy]].forEach(([label,value])=>{
      const card=el('div');card.append(el('span',label),el('strong',scoreText(value)));cards.append(card);
    });summary.append(cards);
    if(m.source_coverage)summary.append(el('p',uiText('评分来源覆盖 ')+m.source_coverage.used+' / '+m.source_coverage.selected+uiText('；仅按已有分数计算'),'lb-small'));
    if(m.ability===null)summary.append(el('p',uiText('所选来源缺少可用分数，或该来源样本不足 10 条'),'lb-small'));
    if(m.value===null)summary.append(el('p',m.price_status==='NO_CURRENT_PUBLIC_ROUTE'?uiText('暂无精确当前渠道价，保留能力记录但不参与性价比计算'):!m.price?uiText('报价不完整，暂不计算性价比'):m.mixed_usd===0?uiText('免费报价，单独比较'):uiText('完整的评分或正价样本不足'),'lb-small'));
    const basis=section(uiText('评分依据与来源'));
    data.sources.filter(x=>x.role==='SCORE').forEach(source=>{
      const score=m.score_breakdown?.[source.id],details=m.details.filter(d=>d.source_id===source.id);
      const box=el('details',null,'lb-disclosure');box.dataset.source=source.id;
      box.open=openIds.has(source.id)||score?.status==='INCLUDED';
      const label=el('summary',source.name);
      const state=score?.status==='INCLUDED'?uiText('参与 ')+score.weight_percent.toFixed(1)+'%':
        score?.status==='NOT_SELECTED'?uiText('未参与'):uiText('此版本或档位暂无评分');
      label.append(el('small',state,'lb-small'));box.append(label);
      const body=el('div',null,'lb-disclosure-body');
      specs(body,[[uiText('场景原始分'),scoreText(score?.raw)],[uiText('归一化百分位'),scoreText(score?.percentile)],
        [uiText('加权贡献'),scoreText(score?.contribution)]]);
      const live=details.find(d=>Object.keys(d.metrics).some(k=>k.startsWith('livebench_')));
      if(live){
        const grid=el('div',null,'lb-category-scores');
        const categories={reasoning:uiText('推理'),coding:uiText('编程'),agentic_coding:uiText('Agent 编程'),math:uiText('数学'),data_analysis:uiText('数据分析'),language:uiText('语言'),instruction_following:uiText('指令遵循')};
        Object.entries(categories).forEach(([key,title])=>{
          const value=live.metrics['livebench_'+key],line=el('div',null,'lb-category-score');
          line.append(el('span',title),el('strong',scoreText(value)));
          const bar=el('span',null,'lb-category-bar'),fill=el('i');
          fill.style.width=(typeof value==='number'?Math.max(0,Math.min(100,value)):0)+'%';bar.append(fill);line.append(bar);grid.append(line);
        });body.append(grid);
      }
      if(!details.length)body.append(el('p',uiText('来源未提供可匹配此版本和档位的数据'),'lb-small'));
      details.forEach(d=>{
        if(d.scope==='MODEL_CAPABILITY_UPPER_ENVELOPE')body.append(el('p',uiText('模型级参考 ')+scoreText(d.metrics.eci)+uiText(' · 未注明思考档位，仅用于本参考行'),'lb-small'));
        if(d.stale)body.append(el('p',uiText('缓存待更新'),'lb-small'));
        body.append(el('p',d.model_id,'lb-source-id'));
        const dateLabel=d.date_semantics==='MODEL_RELEASE_NOT_EVALUATION'?uiText('模型发布日期'):d.date_semantics==='BENCHMARK_RELEASE_NOT_MODEL_DATE'?uiText('基准发布日期'):uiText('来源日期');
        body.append(el('p',uiText('数据版本 ')+(d.benchmark_version||'—')+' · '+dateLabel+' '+(d.as_of||'—'),'lb-small'));
      });
      box.append(body);basis.append(box);
    });
    const cost=section(uiText('性价比口径')),costShares=percentShares([a.ability_weight,a.cost_weight]);
    specs(cost,[[uiText('能力 / 成本'),costShares[0]+'% / '+costShares[1]+'%'],
      [uiText('混合成本'),price(m,'mixed')+' '+codeLabel(displayCode())+' / M'],
      [uiText('参照中位成本'),(a.reference_cost_usd==null?'—':C.format(a.reference_cost_usd,'USD'))+' / M'],
      [uiText('参照样本'),a.cohort_size+uiText(' 条评分 · ')+a.cost_sample_size+uiText(' 项报价')],
      [uiText('输入 / 输出比例'),Math.round(a.input_ratio*100)+' : '+Math.round((1-a.input_ratio)*100)]]);
    const quotes=section(uiText('常规价与汇率')),quote=m.price||m.channel_price;
    specs(quotes,[[uiText('报价渠道'),quote?.source_name||quote?.source_id||uiText('未提供')],
      [uiText('报价更新'),quote?.retrieved_at||uiText('未提供')],
      [uiText('渠道模型'),quote?.model_id||uiText('未提供')],
      [uiText('常规输入'),m.price?C.format(m.price.input,'USD')+' / M':m.price_status==='NO_CURRENT_PUBLIC_ROUTE'?uiText('暂无当前渠道价'):uiText('待核实')],
      [uiText('常规输出'),m.price?C.format(m.price.output,'USD')+' / M':m.price_status==='NO_CURRENT_PUBLIC_ROUTE'?uiText('暂无当前渠道价'):uiText('待核实')]]);
    if(m.channel_price)specs(quotes,[[uiText('当前渠道输入'),C.format(m.channel_price.input,'USD')+' / M'],
      [uiText('当前渠道输出'),C.format(m.channel_price.output,'USD')+' / M']]);
    if(m.price?.verification){const v=m.price.verification;
      specs(quotes,[[uiText('常规价核验'),v.retrieved_at||uiText('未提供')],
        [uiText('已排除优惠'),[...new Set((v.endpoints||[]).filter(e=>e.discount>0).map(e=>Math.round(e.discount*100)+'%'))].join(' / ')||uiText('无')],
        [uiText('匹配供给端'),(v.endpoints||[]).map(e=>e.provider+' · '+e.tag).join(' / ')]]);}
    if(displayCode()!=='USD')specs(quotes,[[uiText('换算输入'),price(m,'input')+' '+codeLabel(displayCode())+' / M'],
      [uiText('换算输出'),price(m,'output')+' '+codeLabel(displayCode())+' / M']]);
    quotes.append(el('p',usableFx()?'1 USD = '+data.fx.unitsPerUsd.CNY.toFixed(4)+' RMB = '+data.fx.unitsPerUsd.JPY.toFixed(4)+' JPY · '+data.fx.rateDate:uiText('汇率暂不可用，可查看上方 USD 原价'),'lb-small'));
    quotes.append(el('p',quoteTip(m),'lb-detail-note'));
  }
  function readDraft() {
    if(!draft) return;
    draft.weights={};
    qa('[data-weight-source]').forEach(input=>{
      const checked=q('[data-check-source="'+input.dataset.weightSource+'"]').checked;
      draft.weights[input.dataset.weightSource]=checked?Number(input.value):0;
    });
    draft.preset='general';draft.input_ratio=Number(q('#lb-ratio').value);
    draft.ability_weight=Number(q('#lb-ability-weight').value);draft.cost_weight=Number(q('#lb-price-weight').value);
    const sum=Object.values(draft.weights).reduce((a,b)=>a+b,0),total=draft.ability_weight+draft.cost_weight;
    const nodes=qa('[data-weight-share]'),shares=percentShares(nodes.map(n=>draft.weights[n.dataset.weightShare]||0));
    nodes.forEach((n,i)=>n.textContent=shares[i]+'%');
    const costs=percentShares([draft.ability_weight,draft.cost_weight]);
    q('#lb-ability-pct').textContent=costs[0]+'%';q('#lb-price-pct').textContent=costs[1]+'%';
    const valid=(!activeScoreSources().length||sum>0)&&total>0&&[...Object.values(draft.weights),draft.ability_weight,draft.cost_weight].every(v=>Number.isFinite(v)&&v>=0&&v<=1000)&&
      qa('#lb-rules input[type="number"]').every(n=>n.value!==''&&n.validity.valid);
    q('#lb-pref-apply').disabled=!valid||saving;
    q('#lb-pref-error').textContent=valid?'':uiText('请输入有效权重；每组至少保留一项正权重');q('#lb-pref-error').hidden=valid;
    q('#lb-pref-dirty').hidden=JSON.stringify(draft)===JSON.stringify(preferenceDraft());
  }
  const activeModelSources = () => data.sources.filter(s=>s.enabled&&s.role!=='FX');
  const activeScoreSources = () => activeModelSources().filter(s=>s.role==='SCORE');
  const preferenceDraft = () => {
    const {ability_weight,cost_weight,input_ratio}=data.preferences;
    const weights=Object.fromEntries(activeScoreSources().map(s=>[s.id,data.preferences.weights[s.id]||0]));
    return {weights,preset:'general',ability_weight,cost_weight,input_ratio};
  };
  function reconcileDraft(previous) {
    const next=preferenceDraft();
    if(!previous)return next;
    for(const key of ['ability_weight','cost_weight','input_ratio'])next[key]=previous[key];
    for(const key of Object.keys(next.weights))if(key in previous.weights)next.weights[key]=previous.weights[key];
    const keys=Object.keys(next.weights);
    if(keys.length&&!Object.values(next.weights).some(v=>v>0)&&JSON.stringify(keys)!==JSON.stringify(Object.keys(previous.weights)))next.weights[keys[0]]=100;
    return next;
  }
  function renderPreferences() {
    const rows=q('#lb-source-rows');rows.replaceChildren();
    const sources=activeModelSources();
    q('#lb-source-head').hidden=!sources.length;
    const hint=q('#lb-source-empty');hint.hidden=sources.length>0&&activeScoreSources().length>0;
    hint.textContent=sources.length?uiText('当前来源提供资料与报价，暂无能力评分。'):uiText('请在设置 → 外部数据来源中启用模型来源。');
    sources.forEach(s=>{
      const row=el('div',null,'lb-source-row'),label=el('label',null,'lb-check'),check=el('input');
      row.dataset.sourceId=s.id;row.dataset.sourceRole=s.role;
      if(s.role==='PRICE') {
        const name=el('span',s.name);name.append(el('small',uiText('资料与报价'),'lb-small'));label.append(name);
        row.append(label,el('span','—','lb-source-readonly'),el('span','—','lb-source-share'));
        rows.append(row);return;
      }
      check.type='checkbox';check.dataset.checkSource=s.id;check.checked=(draft.weights[s.id]||0)>0;
      const name=el('span',s.name);name.append(el('small',s.scored_count?s.scored_count+uiText(' 项评分'):s.status==='NOT_FETCHED'?uiText('尚未更新数据'):s.status==='DISABLED'?uiText('来源已停用'):uiText('暂无可评分数据'),'lb-small'));
      label.append(check,name);const input=el('input',null,'lb-input');input.type='number';input.min='0';input.max='1000';input.step='any';
      input.value=String(draft.weights[s.id]||0);input.dataset.weightSource=s.id;input.setAttribute('aria-label',s.name+uiText('权重'));
      input.disabled=!check.checked;
      const field=el('label',null,'lb-source-weight');
      field.append(el('span',uiText('权重'),'lb-source-inline-label'),input);
      const share=el('span',null,'lb-source-share');share.dataset.weightShare=s.id;
      check.addEventListener('change',()=>{
        if(check.checked)input.value=input.dataset.previousWeight||'100';
        else {input.dataset.previousWeight=input.value;input.value='0';}
        input.disabled=!check.checked;readDraft();
      });
      row.append(label,field,share);rows.append(row);
    });
    q('#lb-ratio').value=String(draft.input_ratio);
    q('#lb-ability-weight').value=String(draft.ability_weight);q('#lb-price-weight').value=String(draft.cost_weight);
    readDraft();
  }
  q('#lb-rule-toggle').addEventListener('click',()=>{
    if(!data)return;owner=q('#lb-rule-toggle');if(!draft)draft=preferenceDraft();changePane('preferences');renderPreferences();q('#lb-inspector-close').focus();
  });
  q('#lb-inspector-close').addEventListener('click',closePane);
  q('#lb-inspector-back').addEventListener('click',closePane);
  q('#lb-rules').addEventListener('input',readDraft);q('#lb-rules').addEventListener('change',readDraft);
  q('#lb-reset-weights').addEventListener('click',()=>{
    const weights=Object.fromEntries(activeScoreSources().map((s,i)=>[s.id,i===0?100:0]));
    draft={weights,preset:'general',ability_weight:70,cost_weight:30,input_ratio:.8};renderPreferences();
  });
  q('#lb-pref-apply').addEventListener('click',async()=>{
    if(!draft||saving)return;readDraft();if(q('#lb-pref-apply').disabled)return;
    saving=true;q('#lb-pref-apply').disabled=true;
    const patch=structuredClone(draft);
    if(!activeScoreSources().length)delete patch.weights;
    const saved=await save(patch);saving=false;
    if(saved) {draft=preferenceDraft();renderPreferences();notify(uiText('偏好已保存，排名已更新'));}
    else {q('#lb-pref-error').textContent=uiText('未保存，请检查当前数据后重试');q('#lb-pref-error').hidden=false;q('#lb-pref-apply').disabled=false;}
  });
  q('#lb-currency').addEventListener('click',async()=>{
    closeMenu(false);if(await save(p=>({currency:['USD','CNY','JPY'][(['USD','CNY','JPY'].indexOf(p.currency)+1)%3]})))void ensureFx(true);
  });
  window.addEventListener('p08:display-currency-changed',async event=>{
    const code=event.detail?.currency==='RMB'?'CNY':event.detail?.currency;
    if(event.detail?.source!=='accounting-panel'||!['USD','CNY','JPY'].includes(code))return;
    if(!data)await load();
    if(data&&data.preferences.currency!==code)await save({currency:code});
    void ensureFx(true);
  });
  q('#lb-starred-only').addEventListener('click',()=>{starredOnly=!starredOnly;if(data)render();});
  q('#lb-clear-filters').addEventListener('click',()=>{filters={};if(data)render();});
  async function pollRefresh() {
    clearTimeout(pollTimer);
    if(document.body.dataset.p08ActiveRoute && document.body.dataset.p08ActiveRoute!=='leaderboard') return;
    const readable=await load();
    // While visible, keep observing the existing 60-second background worker.
    // Polls are local reads, never refresh requests. Initial catch-up uses the
    // same 400 ms cadence as a user-requested refresh.
    if(document.body.dataset.p08ActiveRoute==='leaderboard') {
      const catchingUp=data?.refresh.status==='RUNNING'||(data?.refresh.status==='NOT_FETCHED'&&data?.refresh.automatic_enabled);
      const delays=[400,1000,2000];
      const delay=catchingUp?delays[Math.min(pollStep,delays.length-1)]:60000;
      pollStep=catchingUp?Math.min(pollStep+1,delays.length-1):0;
      if(!readable)pollStep=delays.length-1;
      pollTimer=setTimeout(pollRefresh,delay);
    }
  }
  q('#lb-refresh').addEventListener('click',async()=>{
    fxAttempted=true;fxError='';
    pollStep=0;
    q('#lb-refresh').disabled=true;notify(uiText('正在刷新…'));
    try {await call('settings.leaderboard_refresh');await pollRefresh();}
    catch (_) {notify(uiText('刷新失败 · 请检查桌面连接'));q('#lb-refresh').disabled=false;}
  });
  new MutationObserver(()=>{
    const open=q('#p08-window').dataset.right==='true';
    if(!open&&pane) closePane();
    else if(open&&!pane&&data) {draft=preferenceDraft();changePane('preferences');renderPreferences();}
  }).observe(q('#p08-window'),{attributes:true,attributeFilter:['data-right']});
  // Delegate to the existing shell tooltip element so dynamically rendered headers also work.
  const tip=q('#p08-tooltip');let tipOwner=null;
  function hideTip(){tip.hidden=true;tipOwner?.removeAttribute('aria-describedby');tipOwner=null;}
  function showTip(target){
    hideTip();if(!target?.dataset.p08Tip)return;
    tipOwner=target;tip.textContent=target.dataset.p08Tip;tip.hidden=false;target.setAttribute('aria-describedby',tip.id);
    const r=target.getBoundingClientRect(),b=tip.getBoundingClientRect();
    tip.style.left=Math.max(8,Math.min(r.left,innerWidth-b.width-8))+'px';
    tip.style.top=Math.max(8,Math.min(r.bottom+7,innerHeight-b.height-8))+'px';
  }
  ['pointerover','focusin'].forEach(type=>root.addEventListener(type,e=>{
    const target=e.target.closest?.('[data-p08-tip]');
    if(target && root.contains(target)){e.stopImmediatePropagation();showTip(target);}
  },true));
  root.addEventListener('pointerout',e=>{if(tipOwner&&!tipOwner.contains(e.relatedTarget))hideTip();},true);
  root.addEventListener('focusout',hideTip,true);
  root.addEventListener('keydown',e=>{
    if(e.key==='Escape'){e.preventDefault();hideTip();if(!q('#lb-column-menu').hidden)closeMenu();else if(pane)closePane();}
  });
  document.addEventListener('pointerdown',e=>{if(!q('#lb-column-menu').hidden&&!q('#lb-column-menu').contains(e.target)&&!e.target.closest?.('[data-header]'))closeMenu(false);},true);
  root.addEventListener('scroll',e=>{hideTip();if(q('.lb-table-wrap').contains(e.target))closeMenu(false);},true);
  window.addEventListener('resize',()=>{hideTip();closeMenu(false);});
  window.addEventListener('p08-integrated-route',e=>{
    hideTip();closeMenu(false);clearTimeout(pollTimer);
    if(e.detail.route==='leaderboard')void pollRefresh();
  });
  window.addEventListener('pywebviewready',()=>{if(document.body.dataset.p08ActiveRoute==='leaderboard')void pollRefresh();});
  window.addEventListener('p08:external-sources-changed',()=>{void pollRefresh();});
  new MutationObserver(()=>{ if(data){render();if(pane==='preferences')renderPreferences();else if(pane==='model')renderDetail();} }).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
  window.addEventListener('beforeunload',()=>clearTimeout(pollTimer));
  if(!root.closest('[data-p08-route]'))void load();
})();
