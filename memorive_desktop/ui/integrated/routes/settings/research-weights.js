/* Research weights reuse the shared settings modes, save participant, switches and JSON editor. */
(() => {
 'use strict';
 const page=document.getElementById('memo-weights-page');
 if(!page)return;
 const q=s=>page.querySelector(s),
   t=s=>window.__Desktop_INTEGRATED_APP__?.translate?.(s)||s,
   call=(m,p={})=>window.pywebview.api.call(m,p);
 const el=(tag,text,cls)=>{
   const n=document.createElement(tag);
   if(text!=null)n.textContent=t(text);
   if(cls)n.className=cls;
   return n;
 };
 const labels={Similarity:'内容相似度',Semantic:'语义相关性',Manual:'手动重要性',Rule:'来源与文献属性',Note:'批注与笔记',Citation:'引用使用'};
 const types={synthesis:'综合结论',concept:'概念与方法',annotation:'批注与补充'};
 let data=null,
   entry=null,
   draft=null,
   dirty=false,
   scope='',
   drafts=new Map(),
   overrides=new Map(),
   pickers=[],
   mode='normal',
   loadEpoch=0,
   codeError='',
   codeDirty=false,
   codeText='',
   saving=false;
 const messages={WEIGHT_TOTAL_REQUIRED:'权重合计需要为 100%',WEIGHT_RANGE_INVALID:'权重超出允许范围',WEIGHT_RELEVANCE_RANGE_INVALID:'相关性权重合计需要在 60%–85% 之间',WEIGHT_SETTINGS_INVALID:'请检查配置中的字段与数值',WEIGHT_PROFILE_INVALID:'请使用已配置的模型',WEIGHT_RELEVANCE_REQUIRED:'请保留相关性权重',REVISION_CONFLICT:'设置已变化，请重新载入后修改'};
 const status=(msg,error=false)=>{
   const n=q('#memo-weights-status');
   n.textContent=t(msg);
   n.hidden=!msg;
   n.dataset.error=String(error);
 };
 const error=e=>status(messages[e.message]||'操作未完成，请重试',true),
   sync=()=>window.__Desktop_SETTINGS_BRIDGE__?.syncSaveState();
 const inherited=()=>!!scope&&q('#memo-weight-inherit').checked;
 const valueValid=(c,inherit=false)=>{
  if(!c||Object.keys(c).sort().join()!==Object.keys(entry.defaults).sort().join())return false;
  if(!inherit&&!window.__MEMO_WEIGHT_CONTROLS__.valid(c.weights,entry.limits,entry.relevance_limits))return false;
  if(Object.keys(c.penalties||{}).sort().join()!==Object.keys(types).sort().join()||Object.values(c.penalties).some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0||v>1))return false;
  for(const key of ['semantic_enabled','keep_counterevidence','rule_enrichment'])if(typeof c[key]!=='boolean')return false;
  for(const [key,min,max] of [['review_days',7,730],['interest_days',7,90],['interest_half_life',1,60]])if(!Number.isInteger(c[key])||c[key]<min||c[key]>max)return false;
  if(typeof c.interest_boost!=='number'||c.interest_boost<0||c.interest_boost>.2)return false;
  for(const [key,options] of [['semantic_profile_ref',data.model_options],['embedding_profile_ref',data.embedding_options||[]]])if(c[key]!==null&&!options.some(o=>o.profile_ref===c[key]&&o.eligible))return false;
  if(typeof c.rule_credential_env!=='string'||!/^[A-Za-z_][A-Za-z0-9_]{0,99}$/.test(c.rule_credential_env))return false;
  return true;
 };
 function remember(){
   dirty=true;
   drafts.set(scope,{entry,draft:structuredClone(draft),inherit:inherited(),codeError,codeDirty,codeText});
   sync();
 }
 function mark(){
   codeError='';
   codeDirty=false;
   codeText='';
   remember();
   status('');
   total();
   syncSliders();
   renderPresets();
   renderCode();
 }
 function total(){
   if(!draft)return;
   const sum=Object.values(draft.weights).reduce((a,b)=>a+b,0),r=draft.weights.Similarity+draft.weights.Semantic;
   const n=q('#memo-weight-total');
   n.textContent=t('合计')+' '+Math.round(sum*100)+'% · '+t('相关性')+' '+Math.round(r*100)+'%';
   n.dataset.error=String(!window.__MEMO_WEIGHT_CONTROLS__.valid(draft.weights,entry.limits,entry.relevance_limits));
 }
 function setRange(input,value){
   input.value=value;
   input.style.setProperty('--range-fill',Math.max(0,Math.min(100,(value-Number(input.min))/(Number(input.max)-Number(input.min))*100))+'%');
 }
 function syncSliders(){
   page.querySelectorAll('[data-weight-key]').forEach(n=>{
     const v=Math.round((n.dataset.weightGroup==='weights'?draft.weights[n.dataset.weightKey]:draft.penalties[n.dataset.weightKey])*100);
     setRange(n,v);
     n.closest('.memo-weight-row').querySelector('output').textContent=v+'%';
   });
 }
 function slider(host,key,label,value,group,limits){
  const row=el('label',null,'field-label memo-weight-row'),
    head=el('span',null,'memo-weight-label'),
    out=el('output',Math.round(value*100)+'%'),
    bar=el('span',null,'memo-weight-track');
  head.append(el('span',label),out);
  const input=el('input',null,'form-range');
  input.type='range';
  input.min=limits[0];
  input.max=limits[1];
  input.step=1;
  input.dataset.weightKey=key;
  input.dataset.weightGroup=group;
  input.setAttribute('aria-label',t(label));
  setRange(input,Math.round(value*100));
  input.oninput=()=>{
    if(group==='weights')draft.weights=window.__MEMO_WEIGHT_CONTROLS__.allocate(draft.weights,key,Number(input.value),entry.limits,entry.relevance_limits);else draft.penalties[key]=Number(input.value)/100;
    mark();
  };
  bar.append(el('small',limits[0]+'%'),input,el('small',limits[1]+'%'));
  row.append(head,bar);
  host.append(row);
 }
 function modelControl(host,key,title,options){
  const box=el('div',null,'desktop-settings-model-setting'),
    head=el('span',title,'desktop-settings-model-heading'),
    choice=el('div',null,'desktop-settings-model-choice'),
    dot=el('span',null,'status-dot'),
    name=el('strong'),
    button=el('button','调整','soft-button'),
    menu=el('div',null,'desktop-settings-model-menu');
  button.type='button';
  button.setAttribute('aria-label',t('调整')+' '+t(title));
  menu.setAttribute('role','menu');
  menu.dataset.open='false';
  choice.append(dot,name,button);
  box.append(head,choice,menu);
  host.append(box);
  const picker=window.__Desktop_MODEL_PICKER__.bind({button,menu,label:name,dot,onSelect:option=>{
    draft[key]=option.profileRef||null;
    picker.render(options,option.profileRef);
    mark();
  }});
  picker.render(options,draft[key]||'');
  pickers.push(picker);
 }
 function renderPresets(){
  const host=q('#memo-weight-presets');
  host.replaceChildren();
  let matched=false;
  for(const preset of entry.presets){
   const b=el('button',null,'memo-weight-preset');
   b.type='button';
   b.dataset.preset=preset.id;
   const selected=Object.keys(labels).every((k,i)=>Math.abs(draft.weights[k]*100-preset.weights[i])<1e-6);
   matched||=selected;
   b.setAttribute('aria-pressed',String(selected));
   b.disabled=inherited();
   b.append(el('strong',preset.label),el('span',preset.description));
   b.onclick=()=>{
     draft.weights=Object.fromEntries(Object.keys(labels).map((k,i)=>[k,preset.weights[i]/100]));
     mark();
   };
   host.append(b);
  }
  q('#memo-weight-custom').hidden=matched;
  q('#memo-weight-custom').textContent=t('正在使用自定义配置');
 }
 function renderCode(){
  const input=q('#memo-weight-code'),
    text=codeDirty?codeText:JSON.stringify({schema_version:'MemoResearchWeights-v2',config:draft},null,2);
  if(input.value!==text)input.value=text;
  window.__Desktop_SETTINGS_BRIDGE__?.renderCodeEditor({input,highlight:q('#memo-weight-code-highlight'),lines:q('#memo-weight-code-lines')});
 }
 function modeView(){
  for(const item of page.querySelectorAll('[data-weight-mode]'))item.hidden=item.dataset.weightMode!==mode;
  if(draft&&mode==='developer')renderCode();
 }
 function render(){
  pickers.forEach(p=>p.setOpen(false));
  pickers=[];
  q('#memo-weight-inherit-row').hidden=!scope;
  q('#memo-weight-inherit').checked=drafts.get(scope)?.inherit??entry.inherited;
  q('#memo-weight-sliders').replaceChildren();
  for(const [key,label] of Object.entries(labels))slider(q('#memo-weight-sliders'),key,label,draft.weights[key],'weights',entry.limits[key]);
  q('#memo-weight-penalties').replaceChildren();
  for(const [key,label] of Object.entries(types))slider(q('#memo-weight-penalties'),key,label,draft.penalties[key],'penalties',[0,100]);
  for(const [id,key] of [['review','review_days'],['interest-days','interest_days'],['interest-half','interest_half_life']])q('#memo-weight-'+id).value=draft[key];
  q('#memo-weight-counter').checked=draft.keep_counterevidence;
  setRange(q('#memo-weight-interest-boost'),Math.round(draft.interest_boost*100));
  q('#memo-weight-interest-value').textContent=Math.round(draft.interest_boost*100)+'%';
  q('#memo-weight-enrichment').checked=draft.rule_enrichment;
  q('#memo-weight-metric-env').value=draft.rule_credential_env;
  const models=q('#memo-weight-models');
  models.replaceChildren();
  modelControl(models,'semantic_profile_ref','语义排序模型',[{profileRef:'',model:'不使用',state:'green',note:''},...data.model_options.map(m=>({profileRef:m.profile_ref,model:m.display_name||m.model_name,state:m.eligible?'green':'yellow',note:m.profile_kind,disabled:!m.eligible}))]);
  modelControl(models,'embedding_profile_ref','向量模型',[{profileRef:'',model:'使用关键词检索',state:'green',note:''},...(data.embedding_options||[]).map(m=>({profileRef:m.profile_ref,model:m.display_name||m.model_name,state:m.eligible?'green':'yellow',note:m.profile_kind,disabled:!m.eligible}))]);
  q('#memo-weight-editable').disabled=inherited();
  renderPresets();
  total();
  modeView();
 }
 async function load(newScope=scope){
  const epoch=++loadEpoch;
  scope=newScope;
  const fresh=await call('memo.state',{project:window.__MEMO_WORKSPACE__?.inspect().project||'default',view_thread_id:''});
  if(epoch!==loadEpoch)return;
  data=fresh;
  const selectedScope=scope,
    saved=drafts.get(scope),
    policy=saved?.entry||await call('memo.weight_settings',{project:scope||null});
  if(epoch!==loadEpoch)return;
  const select=q('#memo-weight-project');
  select.replaceChildren();
  const add=(id,name)=>{
    const o=el('option',name);
    o.value=id;
    select.append(o);
  };
  add('','所有项目');
  data.projects.forEach(p=>add(p.id,p.name));
  select.value=selectedScope;
  entry=policy;
  draft=structuredClone(saved?.draft||entry.config);
  codeError=saved?.codeError||'';
  codeDirty=!!saved?.codeDirty;
  codeText=saved?.codeText||'';
  render();
  status(codeError,!!codeError);
  if(!inherited()&&!valueValid(draft))status('已有配置超出新范围，调整后再保存',true);
  q('#memo-interest-list').replaceChildren();
  if(scope)call('memo.interests',{project:scope}).then(result=>{if(epoch===loadEpoch)showInterests(result);}).catch(()=>{});
 }
 function showInterests(result){
   const list=q('#memo-interest-list');
   list.replaceChildren();
   for(const item of result.items||[]){
     const row=el('div',null,'memo-interest-row'),text=el('span',item.label);
     text.setAttribute('data-desktop-no-translate','');
     const remove=el('button','×','icon-button');
     remove.type='button';
     remove.setAttribute('aria-label',t('不再参考')+' '+item.label);
     remove.onclick=async()=>{
       await call('memo.interest_remove',{project:scope,identity:item.id});
       showInterests(await call('memo.interests',{project:scope}));
     };
     row.append(text,remove);
     list.append(row);
   }
 }
 q('#memo-weight-project').onchange=e=>load(e.target.value).catch(error);
 q('#memo-weight-inherit').onchange=async()=>{
   const epoch=loadEpoch,owner=scope,toggle=q('#memo-weight-inherit'),restoreFocus=document.activeElement===toggle;
   toggle.disabled=true;
   try{
     if(toggle.checked){
       overrides.set(owner,structuredClone(draft));
       const global=await call('memo.weight_settings',{});
       if(epoch!==loadEpoch)return;
       draft=structuredClone(drafts.get('')?.draft||global.config);
     }else draft=structuredClone(overrides.get(owner)||draft);
     mark();
     render();
   }catch(e){if(epoch===loadEpoch)error(e);}finally{
     toggle.disabled=false;
     if(restoreFocus&&epoch===loadEpoch&&owner===scope&&document.activeElement===document.body)toggle.focus({preventScroll:true});
   }
 };
 q('#memo-weight-reset').onclick=()=>{
   draft=structuredClone(entry.defaults);
   q('#memo-weight-inherit').checked=false;
   mark();
   render();
 };
 for(const [selector,key] of [['#memo-weight-review','review_days'],['#memo-weight-interest-days','interest_days'],['#memo-weight-interest-half','interest_half_life'],['#memo-weight-interest-boost','interest_boost']])q(selector).oninput=e=>{
   draft[key]=Number(e.target.value)/(key==='interest_boost'?100:1);
   if(key==='interest_boost'){
     setRange(e.target,Number(e.target.value));
     q('#memo-weight-interest-value').textContent=e.target.value+'%';
   }
   mark();
 };
 q('#memo-weight-enrichment').onchange=e=>{
   draft.rule_enrichment=e.target.checked;
   mark();
 };
 q('#memo-weight-metric-env').oninput=e=>{
   draft.rule_credential_env=e.target.value;
   mark();
 };
 q('#memo-weight-counter').onchange=e=>{
   draft.keep_counterevidence=e.target.checked;
   mark();
 };
 q('#memo-weight-code').addEventListener('input',()=>{
  codeDirty=true;
  codeText=q('#memo-weight-code').value;
  codeError='';
  try{
    const d=JSON.parse(codeText);
    if(Object.keys(d).sort().join()!=='config,schema_version'||d.schema_version!=='MemoResearchWeights-v2'||!valueValid(d.config))throw Error('invalid');
    draft=structuredClone(d.config);
    syncSliders();
    total();
    renderPresets();
  }
  catch(e){codeError=t('配置无效，请核对字段、上下限与合计');}
  remember();
  status(codeError,!!codeError);
  renderCode();
 });
 q('#memo-weight-code').addEventListener('scroll',()=>window.__Desktop_SETTINGS_BRIDGE__?.renderCodeEditor({input:q('#memo-weight-code'),highlight:q('#memo-weight-code-highlight'),lines:q('#memo-weight-code-lines')}));
 q('#memo-weight-code-format').onclick=()=>{
   if(codeError)return;
   codeDirty=false;
   codeText='';
   renderCode();
 };
 q('#memo-weight-code-diff').onclick=()=>{
  const body=q('#memo-weight-code-diff-body');
  body.hidden=!body.hidden;
  if(body.hidden)return;
  const before=JSON.stringify({schema_version:'MemoResearchWeights-v2',config:entry.config},null,2),
    after=q('#memo-weight-code').value;
  body.replaceChildren();
  for(const r of window.__Desktop_SETTINGS_BRIDGE__.codeDiff(before,after)){
    const row=el('div',null,'desktop-settings-diff-row desktop-settings-diff-'+r.kind);
    row.append(el('span',r.marker),el('code',r.text||' '));
    row.setAttribute('data-desktop-no-translate','');
    body.append(row);
  }
 };
 q('#memo-weight-preview').onclick=async()=>{
   const b=q('#memo-weight-preview');
   b.disabled=true;
   try{
     const result=await call('memo.weight_preview',{project:scope||window.__MEMO_WORKSPACE__.inspect().project,query:q('#memo-weight-query').value,config:draft});
     const out=q('#memo-weight-preview-result');
     out.replaceChildren();
     for(const [key,title] of [['before','当前设置'],['after','修改后']]){
       const section=el('div');
       section.append(el('h4',title));
       const list=el('ol');
       for(const r of result[key].results){
         const row=el('li',r.title+' · '+r.score.toFixed(3));
         row.setAttribute('data-desktop-no-translate','');
         list.append(row);
       }
       if(!list.children.length)list.append(el('li','暂无匹配资料'));
       section.append(list);
       out.append(section);
     }
     status('');
   }catch(e){error(e);}finally{b.disabled=inherited();}
 };
 function valid(){
   return !drafts.size||!!entry&&[...drafts.values()].every(v=>!v.codeError&&valueValid(v.draft,v.inherit));
 }
 async function save(){
   if(saving)return {status:'BUSY'};
   if(!valid())throw Error('WEIGHT_SETTINGS_INVALID');
   saving=true;
   try{
     for(const [key,value] of [...drafts]){
       await call('memo.weight_save',{project:key||null,expected_revision:value.entry.revision,config:value.draft,inherit:!!key&&value.inherit});
       drafts.delete(key);
     }
     dirty=false;
     codeDirty=false;
     await load();
     status('');
     return {status:'PASS'};
   }finally{
     saving=false;
     sync();
   }
 }
 async function discard(){
   drafts.clear();
   overrides.clear();
   dirty=false;
   codeDirty=false;
   codeError='';
   await load();
   status('');
   sync();
   return {status:'PASS'};
 }
 window.addEventListener('desktop:settings-mode-changed',e=>{if(e.detail.scope==='research-weights'){
   mode=e.detail.mode;
   modeView();
 }});
 window.addEventListener('desktop:settings-category-changed',e=>{
   pickers.forEach(p=>p.setOpen(false));
   if(e.detail.category==='research-weights'){
     window.__Desktop_SETTINGS_UI__?.applySettingsMode(mode);
     load().catch(error);
   }
 });
 for(const event of ['input','change'])page.addEventListener(event,e=>e.stopPropagation());
 // settings.js loads earlier in the page and publishes its bridge as it loads.
 window.__Desktop_SETTINGS_BRIDGE__.registerSaveParticipant('research-weights',{isDirty:()=>dirty,save,discard,validate:valid});
 window.__MEMO_WEIGHTS__={load,save,discard,inspect:()=>({scope,dirty,draft,mode,codeError,valid:valid()})};
})();


