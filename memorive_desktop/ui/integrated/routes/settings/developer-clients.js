/* Local developer clients reuse the settings save, controls and disclosures. */
(() => {
 'use strict';
 const q=s=>document.querySelector(s),t=s=>window.__Desktop_INTEGRATED_APP__?.translate?.(s)||s;
 const call=(m,p={})=>window.pywebview.api.call(m,p),
   el=(tag,text,cls)=>{
     const n=document.createElement(tag);
     if(text!==null)n.textContent=t(text);
     if(cls)n.className=cls;
     return n;
   };
 const scopes={read:'读取与检索',events:'读取事件',import:'导入资料',draft:'提交知识草稿',export:'导出结果',writeback:'回写笔记',automation:'提交自动化任务',model:'允许模型调用'};
 const states={UNAVAILABLE:'历史记录已移除',PAUSED:'已暂停',CANCELLING:'正在取消',QUEUED:'排队中',RUNNING:'执行中',DISPATCHED:'已交给处理流程',COMPLETE:'已完成',ERROR:'失败',CANCELLED:'已取消',INTERRUPTED:'已中断'};
 let data=null,drafts=new Map(),editing=null,automation=null,autoDirty=false,loading=false;
 const dirty=()=>drafts.size>0||autoDirty,sync=()=>window.__Desktop_SETTINGS_BRIDGE__?.syncSaveState();
 function status(text,error=false,scope='clients'){
   const n=q(scope==='clients'?'#memo-dev-status':'#memo-auto-status');
   n.textContent=t(text);
   n.hidden=!text;
   n.dataset.error=String(error);
 }
 const fail=(e,scope='clients')=>status(String(e).includes('REVISION_CONFLICT')?'设置已变化，请重新载入后修改':String(e).includes('CLIENT_DISABLED')?'请先保存并启用此接入':'操作未完成，请检查设置后重试',true,scope);
 function button(label,icon,action){
  const b=el('button',null,'icon-button desktop-toolbar-icon desktop-toolbar-soft desktop-action-'+icon);
  b.type='button';
  b.setAttribute('aria-label',t(label));
  b.dataset.desktopTip=t(label);
  b.addEventListener('click',()=>Promise.resolve(action()).catch(fail));
  return b;
 }
 function checkbox(label,key,checked,onchange){
  const row=el('label',null,'memo-dev-check'),box=el('input',null,'desktop-batch-check');
  box.type='checkbox';
  box.checked=checked;
  box.dataset.value=key;
  box.addEventListener('change',()=>onchange(box.checked));
  row.append(box,el('span',label));
  return row;
 }
 function mark(key,row){
   drafts.set(key,row);
   sync();
   status('');
 }
 function renderClients(){
  const list=q('#memo-dev-clients');
  list.replaceChildren();
  for(const saved of data.clients){
   const c=drafts.get(saved.id)||saved,row=el('div',null,'desktop-settings-control-row memo-dev-client');
   row.dataset.clientId=c.id;
   const text=el('span',null),name=el('strong',c.name);
   name.dataset.desktopNoTranslate='true';
   text.append(name,el('small',c.projects.map(id=>data.projects.find(p=>p.id===id)?.name||id).join(' · ')));
   const actions=el('span',null,'desktop-settings-inline');
   const enabled=el('button',null,'switch');
   enabled.type='button';
   enabled.setAttribute('role','switch');
   enabled.setAttribute('aria-label',t('启用接入')+' · '+c.name);
   enabled.setAttribute('aria-checked',String(c.enabled));
   enabled.onclick=()=>{
     mark(c.id,{...c,enabled:!c.enabled});
     renderClients();
   };
   const edit=button('编辑接入','edit',()=>editClient(c));
   edit.disabled=editing!==null&&editing!==c.id;
   const copy=button('复制接入配置','code',()=>copyConfig(c.id));
   copy.disabled=!c.enabled||drafts.has(c.id);
   actions.append(enabled,edit,copy);
   row.append(text,actions);
   list.append(row);
  }
  if(!data.clients.length)list.append(el('p','添加需要接入的本地工具','memo-dev-empty'));
  q('#memo-dev-add').disabled=editing!==null;
 }
 function editClient(c=null){
  editing=c?.id||'new';
  const value=structuredClone(c||{name:'',enabled:true,projects:['default'],connection_ids:[],scopes:['read','events'],revision:0});
  mark(editing,value);
  q('#memo-dev-editor').hidden=false;
  q('#memo-dev-name').value=value.name;
  const fields=[['projects','#memo-dev-projects',data.projects],['connection_ids','#memo-dev-connections',data.connections]];
  for(const [key,selector,items] of fields){
   const target=q(selector);
   target.replaceChildren();
   for(const item of items){
    const caption=key==='projects'?item.name:item.name+' · '+(data.projects.find(p=>p.id===item.project)?.name||item.project);
    target.append(checkbox(caption,item.id,value[key].includes(item.id),checked=>{
     const d=drafts.get(editing);
     d[key]=checked?[...new Set([...d[key],item.id])]:d[key].filter(i=>i!==item.id);
     mark(editing,d);
    }));
   }
   if(!items.length)target.append(el('small','请先在连接与同步中添加资料文件夹'));
  }
  q('#memo-dev-scopes').replaceChildren(...Object.entries(scopes).map(([key,label])=>checkbox(label,key,value.scopes.includes(key),checked=>{
   const d=drafts.get(editing);
   d.scopes=checked?[...new Set([...d.scopes,key])]:d.scopes.filter(i=>i!==key);
   mark(editing,d);
  })));
  renderClients();
  q('#memo-dev-name').focus();
 }
 function valid(){
  for(const d of drafts.values()){
   if(!d.name.trim()||!d.projects.length||d.connection_ids.some(id=>!d.projects.includes(data.connections.find(c=>c.id===id)?.project)))return false;
  }
  return !autoDirty||(Number.isInteger(automation.max_queued)&&automation.max_queued>=1&&automation.max_queued<=100);
 }
 async function copy(text){
  const n=el('textarea',null);
  n.value=text;
  n.style.cssText='position:fixed;opacity:0';
  document.body.append(n);
  n.select();
  const ok=document.execCommand('copy');
  n.remove();
  if(!ok)throw Error('COPY_FAILED');
  status('已复制');
 }
 async function copyConfig(id){
   const value=await call('memo.developer_config',{client_id:id});
   await copy(JSON.stringify(value.mcp,null,2));
   q('#memo-dev-config').textContent=JSON.stringify(value,null,2);
   q('#memo-dev-config').hidden=false;
 }
 function renderAutomation(){
  q('#memo-auto-enabled').setAttribute('aria-checked',String(automation.enabled));
  q('#memo-auto-limit').value=automation.max_queued;
  const list=q('#memo-auto-jobs');
  list.replaceChildren();
  for(const job of data.jobs){
    const row=el('div',null,'desktop-settings-control-row');
    row.append(el('span',data.clients.find(c=>c.id===job.client_id)?.name||'自动化任务'),el('small',states[job.status]||job.status));
    list.append(row);
  }
  if(!data.jobs.length)list.append(el('p','暂无自动化任务','memo-dev-empty'));
 }
 async function load(){
  if(loading)return;
  loading=true;
  try{
    data=await call('memo.developer_state');
    if(!autoDirty)automation=structuredClone(data.automation);
    renderClients();
    renderAutomation();
  }
  finally{loading=false;}
 }
 async function save(){
  if(!valid())throw Error('CLIENT_SETTINGS_INVALID');
  try{
   for(const [id,d] of [...drafts]){
    const config=Object.fromEntries(['name','enabled','projects','connection_ids','scopes'].map(k=>[k,d[k]]));
    await call('memo.developer_save',{config,client_id:id==='new'?null:id,expected_revision:d.revision||0});
    drafts.delete(id);
    if(editing===id){
      editing=null;
      q('#memo-dev-editor').hidden=true;
    }
   }
   if(autoDirty){
     automation=await call('memo.automation_save',{config:{enabled:automation.enabled,max_queued:automation.max_queued},expected_revision:automation.revision});
     autoDirty=false;
   }
   await load();
   status('');
   status('',false,'automation');
   return {status:'PASS'};
  }catch(e){
    fail(e,autoDirty?'automation':'clients');
    throw e;
  }finally{sync();}
 }
 async function discard(){
   drafts.clear();
   autoDirty=false;
   editing=null;
   q('#memo-dev-editor').hidden=true;
   await load();
   sync();
 }
 const openClients=()=>{
   window.__Desktop_INTEGRATED_APP__.activate('settings');
   q('#memo-settings-category').click();
   q('[data-memo-settings-category=agent]').click();
   q('#memo-agent-config-details').open=true;
 };
 const openAutomation=()=>{
   window.__Desktop_INTEGRATED_APP__.activate('settings');
   q('[data-category=behavior]').click();
   q('#memo-automation-details').open=true;
 };
 function init(){
  window.__Desktop_SETTINGS_BRIDGE__.registerSaveParticipant('developer-access',{isDirty:dirty,validate:valid,save,discard});
  q('#memo-dev-add').onclick=()=>editClient();
  q('#memo-dev-cancel').onclick=()=>{
    drafts.delete(editing);
    editing=null;
    q('#memo-dev-editor').hidden=true;
    renderClients();
    sync();
  };
  q('#memo-dev-name').oninput=()=>{
    const d=drafts.get(editing);
    d.name=q('#memo-dev-name').value;
    mark(editing,d);
  };
  q('#memo-auto-enabled').onclick=()=>{
    automation.enabled=q('#memo-auto-enabled').getAttribute('aria-checked')==='true';
    autoDirty=true;
    sync();
  };
  q('#memo-auto-limit').oninput=()=>{
    automation.max_queued=Number(q('#memo-auto-limit').value);
    autoDirty=true;
    sync();
  };
  q('#memo-auto-refresh').onclick=()=>window.__Desktop_UI_CONTROLS__.busy(q('#memo-auto-refresh'),async()=>{try{
    await load();
    status('',false,'automation');
  }catch(e){fail(e,'automation');}});
  q('#memo-dev-refresh').onclick=()=>window.__Desktop_UI_CONTROLS__.busy(q('#memo-dev-refresh'),load).catch(fail);
  q('#memo-api-sdk').onclick=()=>window.pywebview.api.open_developer_sdk().catch(fail);
  q('#memo-api-clients-link').onclick=openClients;
  q('#memo-api-automation-link').onclick=openAutomation;
  for(const parent of document.querySelectorAll('[data-memo-developer]')){
   for(const event of ['input','change'])parent.addEventListener(event,e=>e.stopPropagation());
  }
  q('#memo-agent-config-details').addEventListener('toggle',()=>{if(q('#memo-agent-config-details').open)load().catch(fail);});
  q('#memo-automation-details').addEventListener('toggle',()=>{if(q('#memo-automation-details').open)load().catch(e=>fail(e,'automation'));});
  // The desktop service answers once pywebview is ready.
  if(window.pywebview?.api?.call)load().catch(fail);
  else window.addEventListener('pywebviewready',()=>load().catch(fail),{once:true});
 }
 init();   // settings.js, loaded earlier, has published its bridge; the panel's markup is in the page
 window.__MEMO_DEVELOPER__=Object.freeze({load,save,discard,openClients,openAutomation,inspect:()=>({loaded:!!data,dirty:dirty(),editing,clients:data?.clients.length||0,api_version:data?.api_version})});
})();

