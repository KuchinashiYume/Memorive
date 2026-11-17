/* The research connections panel in settings: the folders, Obsidian vaults, Zotero libraries and bibliography files a
   project draws on, their previews, imports and exports (memo.connection_*). Publishes window.__MEMO_CONNECTIONS__. */
(() => {
 'use strict';
 const q=s=>document.querySelector(s),
   make=(tag,text,cls)=>{
     const e=document.createElement(tag);
     if(text!=null)e.textContent=text;
     if(cls)e.className=cls;
     return e;
   };
 const t=s=>window.__Desktop_INTEGRATED_APP__?.translate?.(s)||s;
 const call=(m,p={})=>window.pywebview.api.call(m,p),
   currentProject=()=>window.__MEMO_WORKSPACE__?.inspect().project||'default';
 const names={obsidian:'Obsidian',zotero:'Zotero',folder:'资料文件夹',bibliography:'书目文件'};
 const statusNames={COMPLETE:'已完成',ERROR:'需要处理',PARTIAL:'部分完成',INTERRUPTED:'已中断',QUEUED:'已排队',RUNNING:'处理中',DISPATCHED:'已交给主线',active:'可检索',changed:'来源已更新',unavailable:'来源不可用',SYNCED:'已回写',PROCESSING:'处理中',DEDUPLICATED:'已关联已有文献'};
 const statusLabel=value=>statusNames[value]?t(statusNames[value]):value;
 let data=null,
   connection=null,
   opened=false,
   timer=null,
   busy=false,
   editing=false,
   activeTab='documents',
   refreshSequence=0,
   lastNotice=null;
 function notice(text,bad=false){
   const e=editing?q('#memo-connection-editor-status'):q('#memo-connection-status');
   if(!e)return;
   lastNotice={text,bad};
   const parts=Array.isArray(text)?text:[text];
   e.textContent=parts.map(v=>statusNames[v]?statusLabel(v):typeof v==='string'?t(v):v).join('');
   e.hidden=!e.textContent;
   e.dataset.error=String(bad);
 }
 function error(e){
   const errors={CONNECTION_NAME_REQUIRED:'请填写连接名称',CONNECTION_ROOT_INVALID:'请选择资料文件夹',CONNECTOR_SELECTION_REQUIRED:'请选择需要导入的资料',REVISION_CONFLICT:'连接已变化，请重新打开编辑',CONNECTION_CHANGED_REPREVIEW:'连接已变化，请重新预览资料'};
   notice(errors[e.message]||e.message||String(e),true);
 }
 const icons={'关闭':'cancel','保存连接':'save','选择文件夹':'folder-open','编辑连接':'edit','查看与导出结果':'preview','以标题提问':'message','查看主线任务':'current-task','交给主线处理':'workflow','重试未完成资料':'refresh','重试此请求':'refresh','关联本地附件':'file-plus','导入所选资料':'import','导出所选结果':'export','复制配置':'copy'};
 function button(text,fn,cls='soft-button'){
   const kind=icons[text],
     e=make('button',kind?null:t(text),kind?'icon-button desktop-toolbar-icon desktop-toolbar-soft desktop-action-'+kind:cls);
   e.type='button';
   if(kind){
     e.setAttribute('aria-label',t(text));
     e.dataset.desktopTip=t(text);
   }
   e.onclick=async()=>{
     e.disabled=true;
     try{await fn();}catch(err){error(err);}finally{e.disabled=false;}
   };
   return e;
 }
 function closeEditor(){
   const d=q('#memo-connection-editor');
   d.hidden=true;
   d.replaceChildren();
   editing=false;
 }
 function inlinePanel(title){
   q('#memo-connection-status').textContent='';
   lastNotice=null;
   const d=q('#memo-connection-editor');
   d.replaceChildren();
   d.hidden=false;
   editing=true;
   const head=make('header',null,'memo-inline-heading'),
     body=make('div',null,'desktop-settings-form-stack'),
     foot=make('footer',null,'memo-actions'),
     status=make('p',null,'memo-status');
   status.id='memo-connection-editor-status';
   status.setAttribute('role','status');
   status.hidden=true;
   head.append(make('strong',t(title)),button('关闭',closeEditor));
   d.append(head,body,status,foot);
   d.close=closeEditor;
   d.scrollIntoView({block:'nearest'});
   return {d,body,foot};
 }
 function selectTab(value){
   activeTab=value;
   for(const key of ['documents','jobs']){
     const b=q(key==='documents'?'#memo-show-documents':'#memo-show-sync-jobs'),selected=key===value;
     q('#memo-connection-'+key).hidden=!selected;
     b.setAttribute('aria-selected',String(selected));
     b.setAttribute('aria-pressed',String(selected));
     b.tabIndex=selected?0:-1;
   }
 }
 function counts(){
   const id=connection?.id;
   for(const [key,number] of [['documents',data.documents.filter(d=>d.connection_id===id).length],['jobs',data.jobs.filter(j=>j.connection_id===id).length+data.actions.filter(j=>j.connection_id===id).length]])q('[data-count="'+key+'"]').textContent=number;
   selectTab(activeTab);
 }
 function field(label,value='',type='input'){
   const wrap=make('label',null,'field-label'),el=make(type,null,type==='select'?'select':'field');
   wrap.append(make('span',t(label)),el);
   if(type!=='select')el.value=value;
   return {wrap,el};
 }
 function option(select,value,text){
   const o=make('option',t(text));
   o.value=value;
   select.append(o);
 }
 async function refresh(){
  const requestedProject=currentProject(),sequence=++refreshSequence;
  const result=await call('memo.connections_state',{project:requestedProject});
  if(requestedProject!==currentProject()||sequence!==refreshSequence)return data;
  if(data?.project&&data.project!==requestedProject)closeEditor();
  data={...result,project:requestedProject};
  connection=data.connections.find(c=>c.id===connection?.id)||data.connections[0]||null;
  render();
  return data;
 }
 function render(){
  const list=q('#memo-connections-list');
  list.replaceChildren();
  for(const c of data.connections){
    const b=button('',async()=>{
      closeEditor();
      connection=c;
      render();
    },'memo-connection-item');
    b.append(make('strong',c.name),make('small',(names[c.provider]?t(names[c.provider]):c.provider)+' · '+t(c.enabled?'已启用':'已暂停')));
    b.setAttribute('aria-current',String(c.id===connection?.id));
    list.append(b);
  }
  const detail=q('#memo-connection-detail');
  detail.replaceChildren();
  q('#memo-connection-actions').hidden=!connection;
  q('.memo-connection-sidebar').hidden=!data.connections.length;
  q('#memo-connection-import').disabled=!connection?.enabled;
  counts();
  if(!connection){
    q('#memo-connection-documents').replaceChildren(make('div',t('尚未添加资料连接'),'memo-connection-empty'));
    q('#memo-connection-jobs').replaceChildren(make('div',t('暂无导入或处理任务。'),'memo-connection-empty'));
    return;
  }
  detail.append(make('h3',connection.name),make('p',connection.root,'memo-connection-path'));
  const controls=make('div',null,'memo-actions');
  controls.append(make('span',t(connection.writeback?'允许结果回写':'结果回写未启用'),'memo-connection-badge'),button('编辑连接',()=>edit(connection)),make('span',t(data.desktop_online?'Memo 已连接':'等待 Memo 处理服务'),'memo-connection-badge'));
  detail.append(controls);
  const docs=q('#memo-connection-documents');
  docs.replaceChildren();
  const rows=data.documents.filter(d=>d.connection_id===connection.id);
  if(!rows.length)docs.append(make('div',t('当前连接尚未导入资料。'),'memo-connection-empty'));
  for(const d of rows){
    const row=make('article',null,'memo-connection-row');
    row.append(make('h4',d.title),make('span',statusLabel(d.state),'memo-connection-badge'));
   row.append(make('p',d.path?d.path:'仅书目信息 · 可重新预览书目并关联附件','memo-connection-path'));
   if(d.processing)row.append(make('p',t('主线处理：')+statusLabel(d.processing.state)+(d.processing.job_id?' · '+d.processing.job_id:'')));
   const actions=make('div',null,'memo-actions');
   if(d.state==='active')actions.append(button('查看与导出结果',()=>outputs(d)),button('以标题提问',async()=>{
     await window.__MEMO_WORKSPACE__.refresh();
     window.__Desktop_INTEGRATED_APP__.activate('chat');
     q('#memo-question').value=d.title;
     q('#memo-question').focus();
   }));
   if(d.path&&d.state==='active')actions.append(button(d.processing?'查看主线任务':'交给主线处理',async()=>{
    if(d.processing){
      window.__Desktop_INTEGRATED_APP__.activate('inbox');
      return;
    }
    const r=await call('memo.connection_queue',{connection_id:connection.id,source_id:d.id,kind:'process',request_id:crypto.randomUUID()});
    notice('已排队，使用主线已保存的模型与审核设置。');
    await refresh();
   }));
    row.append(actions);
    docs.append(row);
  }
  const jobs=q('#memo-connection-jobs');
  jobs.replaceChildren();
  for(const job of data.jobs.filter(j=>j.connection_id===connection.id)){
    const row=make('article',null,'memo-connection-row'),done=job.steps.filter(s=>s.status==='COMPLETE').length;
    row.append(make('h4',statusLabel(job.status)+' · '+done+'/'+job.steps.length),make('p',t('本地导入')+' · '+(job.elapsed_ms??0)+' ms · '+t('模型 API 费用 0')));
   for(const s of job.steps.filter(s=>s.status==='ERROR'))row.append(make('p',s.error,'memo-connection-warning'));
   if(['PARTIAL','ERROR','INTERRUPTED'].includes(job.status))row.append(button('重试未完成资料',async()=>{
     await call('memo.connection_retry',{job_id:job.id});
     await refresh();
   }));
    jobs.append(row);
  }
  for(const a of data.actions.filter(a=>a.connection_id===connection.id)){
    const row=make('article',null,'memo-connection-row');
    row.append(make('h4',t(a.kind==='ask'?'插件提问':a.kind==='agent'?'Agent 研究':'主线处理')+' · '+statusLabel(a.status)),make('p',a.error||a.question||'从插件或连接页面发起'));
    if(a.result?.job_id)row.append(make('p',a.result.job_id));
    if(['ERROR','INTERRUPTED'].includes(a.status))row.append(button('重试此请求',async()=>{
      await call('memo.connection_action_retry',{action_id:a.id});
      await refresh();
    }));
    jobs.append(row);
  }
  if(!jobs.children.length)jobs.append(make('div',t('暂无导入或处理任务。'),'memo-connection-empty'));
 }
 async function edit(existing){
  const m=inlinePanel(existing?'编辑连接':'添加连接'),
    name=field('连接名称',existing?.name||''),
    provider=field('软件或来源','','select'),
    root=field('允许读取的资料根目录',existing?.root||'');
  for(const [k,v] of Object.entries(names))option(provider.el,k,v);
  provider.el.value=existing?.provider||'obsidian';
  provider.el.disabled=!!existing;
  root.el.readOnly=true;
  const choose=button('选择文件夹',async()=>{
    const r=await window.pywebview.api.memo_pick_vault();
    if(r.path)root.el.value=r.path;
  });
  choose.disabled=!!existing;
  root.wrap.classList.add('memo-path-control');
  root.wrap.append(choose);
  const enabled=make('input'),write=make('input');
  enabled.type=write.type='checkbox';
  enabled.checked=existing?.enabled??true;
  write.checked=existing?.writeback??false;
  const a=make('label',null,'switch-row'),b=make('label',null,'switch-row');
  for(const input of [enabled,write]){
    input.className='switch memo-settings-toggle';
    input.setAttribute('role','switch');
  }
  a.append(make('span',t('启用此连接')),enabled);
  b.append(make('span',t('允许插件回写 memo 管理的笔记内容')),write);
  const syncWrite=()=>{b.hidden=!['obsidian','zotero'].includes(provider.el.value);};
  provider.el.addEventListener('change',syncWrite);
  syncWrite();
  m.body.append(name.wrap,provider.wrap,root.wrap,a,b);
  name.el.focus();
  m.foot.append(button('保存连接',async()=>{
    if(!name.el.value.trim()){
      name.el.focus();
      throw Error('CONNECTION_NAME_REQUIRED');
    }
    if(!root.el.value){throw Error('CONNECTION_ROOT_INVALID');}
    connection=await call('memo.connection_save',{project:currentProject(),name:name.el.value,provider:provider.el.value,root:root.el.value,enabled:enabled.checked,writeback:['obsidian','zotero'].includes(provider.el.value)&&write.checked,connection_id:existing?.id||null,expected_revision:existing?.revision||0});
    m.d.close();
    await refresh();
  },'primary-button'));
 }
 async function intake(){
  if(!connection?.enabled)throw Error('请先启用连接');
  const owner=connection;
  const selected=await window.pywebview.api.memo_pick_materials();
  if(!selected.paths?.length||connection?.id!==owner.id)return;
  const bibliography=selected.paths.find(p=>/\.(ris|bib|bibtex|xml)$/i.test(p));
  if(bibliography&&selected.paths.length!==1)throw Error('书目文件请单独预览，再逐条关联附件');
  const params={connection_id:owner.id,...(bibliography?{bibliography}:{paths:selected.paths})};
  let preview=await call('memo.connection_preview',params);
  const mapping={};
  if(connection?.id!==owner.id)return;
  const m=inlinePanel('预览导入资料');
  const checks=new Map();
  function show(){
    m.body.replaceChildren();
    checks.clear();
    m.body.append(make('p',t('新资料')+' '+preview.counts.new+' · '+t('更新')+' '+preview.counts.updated+' · '+t('未变化')+' '+preview.counts.unchanged));
   for(const item of preview.items){
     const row=make('div',null,'memo-connection-preview-item'),check=make('input'),body=make('div');
     check.type='checkbox';
     check.checked=true;
     checks.set(item.source_id,check);
     body.append(make('strong',item.title),make('small',item.path||'仅书目 · 尚未关联全文'));
    if(item.attachment_suggestions?.length)body.append(make('small',t('书目中的附件提示：')+item.attachment_suggestions.join('；')));
    if(bibliography)body.append(button('关联本地附件',async()=>{
      const r=await window.pywebview.api.memo_pick_materials();
      if(r.paths?.[0]){
        mapping[item.source_key]=r.paths[0];
        preview=await call('memo.connection_preview',{...params,attachment_map:mapping});
        show();
      }
    }));
    row.append(check,body);
     m.body.append(row);
   }
  }
  show();
  m.foot.append(button('导入所选资料',async()=>{
    busy=true;
    try{
      const r=await call('memo.connection_commit',{preview_id:preview.id,preview_hash:preview.preview_hash,selected_ids:[...checks].filter(([,c])=>c.checked).map(([id])=>id)});
      m.d.close();
      await refresh();
      selectTab('jobs');
      notice([r.status,' · ',r.steps.length,' ','项资料']);
      q('#memo-connection-jobs').scrollIntoView({block:'nearest'});
    }finally{busy=false;}
  },'primary-button'));
 }
 async function outputs(d){
   const owner=connection;
   const r=await call('memo.connection_outputs',{connection_id:owner.id,source_id:d.id}),
     m=inlinePanel(d.title),
     checks=[];
   for(const item of r.items){
     const label=make('label',null,'memo-check'),input=make('input');
     input.type='checkbox';
     input.checked=true;
     label.append(input,make('span',item.title+' · '+item.kind));
     m.body.append(label);
     checks.push([input,item.id]);
     const details=make('details');
     details.append(make('summary',t('预览内容')),make('pre',item.markdown));
     m.body.append(details);
   }
   if(!checks.length)m.body.append(make('div',t('暂无可导出的结果'),'memo-connection-empty'));
   m.foot.append(button('导出所选结果',async()=>{
     const out=await call('memo.connection_export',{connection_id:owner.id,source_id:d.id,output_ids:checks.filter(([c])=>c.checked).map(([,id])=>id)});
     m.body.append(make('p',out.path,'memo-connection-path'));
     notice('结果已导出');
   },'primary-button'));
 }
 async function setup(){
   const owner=connection,config=await window.pywebview.api.memo_agent_config();
   if(connection?.id!==owner?.id)return;
   const m=inlinePanel('插件接入');
   const rows=[['Memorive 工具',config.helper||config.mcpServers?.memo?.command||''],['研究工作区',config.workspace||''],['连接 ID',owner.id],[names[owner.provider]||'插件',owner.provider==='obsidian'?config.obsidian_plugin:owner.provider==='zotero'?config.zotero_plugin:'']];
   for(const [label,value] of rows){
     if(!value)continue;
     const f=field(label,value);
     f.el.readOnly=true;
     f.el.dataset.desktopNoTranslate='';
     m.body.append(f.wrap);
   }
   const text=rows.filter(([,v])=>v).map(([k,v])=>t(k)+': '+v).join('\n');
   m.foot.append(button('复制配置',async()=>{
     if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);else{
       const a=make('textarea');
       a.value=text;
       m.body.append(a);
       a.select();
       if(!document.execCommand('copy'))throw Error('复制失败');
       a.remove();
     }
     notice('已复制');
   }));
 }

 function open(){q('#memo-connections-details').open=true;}
 function close(){q('#memo-connections-details').open=false;}
 if(!q('#memo-connections-panel'))return;
 q('#memo-connections-details').addEventListener('toggle',()=>{
   opened=q('#memo-connections-details').open;
   q('#memo-connections-open').setAttribute('aria-expanded',String(opened));
   if(opened)refresh().catch(error);
 });
 q('#memo-connection-new').onclick=()=>edit().catch(error);
 q('#memo-connection-import').onclick=()=>intake().catch(error);
 q('#memo-connection-refresh').onclick=async()=>{
   try{
     const r=await call('memo.connection_refresh',{connection_id:connection.id});
     await refresh();
     notice(t('来源已更新')+' '+r.changed.length+' · '+t('不可用')+' '+r.missing.length+' · '+t('变化的资料请重新选择并预览。'));
   }catch(e){error(e);}
 };
 q('#memo-connection-setup').onclick=()=>setup().catch(error);
 for(const [id,key] of [['#memo-show-documents','documents'],['#memo-show-sync-jobs','jobs']]){
   const tab=q(id);
   tab.onclick=()=>selectTab(key);
   tab.onkeydown=e=>{
     if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
       e.preventDefault();
       const next=e.key==='Home'?'documents':e.key==='End'?'jobs':activeTab==='documents'?'jobs':'documents';
       selectTab(next);
       q(next==='documents'?'#memo-show-documents':'#memo-show-sync-jobs').focus();
     }
   };
 }
 const helpCopy='添加 Obsidian、Zotero、资料文件夹或书目来源后，用导入图标选择材料。资料与结果显示已导入资料及导出内容；导入与任务显示进度、失败和重试。仅读取你选择的材料；回写只修改 Memo 管理的笔记内容。';
 window.__Desktop_SETTINGS_CONTROLS__?.help(q('#memo-connections-help'),'连接与同步帮助',helpCopy);

 timer=setInterval(()=>{
   if(opened&&q('#memo-connections-panel').getClientRects().length&&!busy&&!editing)refresh().catch(error);
 },2500);
 new MutationObserver(()=>{if(opened&&data){
   render();
   if(lastNotice)notice(lastNotice.text,lastNotice.bad);
 }}).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
 window.__MEMO_CONNECTIONS__={open,close,refresh,inspect:()=>({opened,connection_id:connection?.id,document_count:data?.documents.length||0,activeTab,editing})};
})();

