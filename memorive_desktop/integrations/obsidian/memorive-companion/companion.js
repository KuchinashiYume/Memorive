'use strict';
const {ItemView,Modal,Notice,MarkdownView,TFile}=require('obsidian');
const path=require('path'),{createHash,randomUUID}=require('crypto');
const TYPE='memorive-research-sidebar';
const hash=text=>createHash('sha256').update(JSON.stringify(text),'utf8').digest('hex');
const labels={COMPLETE:'已完成',PARTIAL:'部分完成',ERROR:'需要处理',QUEUED:'等待 Memo',RUNNING:'处理中',DISPATCHED:'已提交',INTERRUPTED:'已中断',active:'可检索',changed:'来源已变化',unavailable:'来源不可用'};
function button(root,text,fn){const b=root.createEl('button',{text});b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){new Notice(e.message);}finally{b.disabled=false;}};return b;}
function modal(plugin,title){const m=new Modal(plugin.app);m.contentEl.createEl('h2',{text:title});return m;}

function install(plugin,invoke,ResearchModal){
 plugin.connector=(method,params={})=>{
  if(!plugin.settings.connection)throw Error('请在插件设置中选择 Memo 连接');
  return invoke(plugin.settings,method,{connection_id:plugin.settings.connection,...params});
 };
 plugin.openWorkbench=async()=>{let leaf=plugin.app.workspace.getLeavesOfType(TYPE)[0];if(!leaf){leaf=plugin.app.workspace.getRightLeaf(false);await leaf.setViewState({type:TYPE,active:true});}plugin.app.workspace.revealLeaf(leaf);return leaf.view;};
 plugin.previewFiles=async files=>{
  const base=plugin.app.vault.adapter.getBasePath(),paths=files.filter(f=>f instanceof TFile&&['md','txt','pdf'].includes(f.extension)).map(f=>path.join(base,f.path));
  if(!paths.length)throw Error('请选择 PDF 或文本笔记');
  const p=await plugin.connector('connector.preview',{paths}),m=modal(plugin,'预览送往 Memo 的资料'),choices=[];
  m.contentEl.createEl('p',{text:`新资料 ${p.counts.new} · 更新 ${p.counts.updated} · 未变化 ${p.counts.unchanged}`});
  for(const row of p.items){const label=m.contentEl.createEl('label',{cls:'memorive-choice'}),check=label.createEl('input',{type:'checkbox'});check.checked=true;label.createSpan({text:row.title});choices.push([check,row.source_id]);}
  button(m.contentEl,'确认导入',async()=>{const r=await plugin.connector('connector.commit',{preview_id:p.id,preview_hash:p.preview_hash,selected_ids:choices.filter(([x])=>x.checked).map(([,id])=>id)});new Notice(labels[r.status]||r.status);m.close();const view=await plugin.openWorkbench();await view.refresh();});m.open();return m;
 };
 plugin.writeResult=async(source_id,output_ids,targetPath)=>{
  const folder=(plugin.settings.outputFolder||'Memorive').replace(/\\/g,'/');
  if(folder.startsWith('/')||folder.split('/').some(p=>!p||p==='..'||p.startsWith('.')))throw Error('结果文件夹必须位于当前 Vault 内');
  const target=targetPath||folder+'/'+source_id+'.md';
  if(target.startsWith('/')||target.includes('..')||!target.endsWith('.md'))throw Error('笔记路径无效');
  let file=plugin.app.vault.getAbstractFileByPath(target);const current=file?await plugin.app.vault.read(file):'';
  const plan=await plugin.connector('connector.writeback_prepare',{source_id,external_note_id:target,existing_text:current,output_ids});
  if(plan.status==='CONFLICT'){
   const m=modal(plugin,'检测到笔记修改');m.contentEl.createEl('p',{text:'memo 管理的内容已被修改。原笔记已保留，可以将结果另存为新笔记。'});m.contentEl.createEl('pre',{text:plan.proposed_markdown});
   button(m.contentEl,'另存为新笔记',async()=>{await plugin.writeResult(source_id,output_ids,folder+'/'+source_id+'-'+Date.now()+'.md');m.close();});m.open();return {status:'CONFLICT'};
  }
  if(file){await plugin.app.vault.process(file,text=>{if(hash(text)!==plan.expected_text_hash)throw Error('笔记在预览后又发生修改，请重试');return plan.text;});}
  else{
   const parts=target.split('/');parts.pop();let dir='';for(const part of parts){dir=dir?dir+'/'+part:part;if(!plugin.app.vault.getAbstractFileByPath(dir))await plugin.app.vault.createFolder(dir);}
   file=await plugin.app.vault.create(target,plan.text);
  }
  const written=await plugin.app.vault.read(file);
  if(hash(written)!==plan.text_hash)throw Error('写入后的笔记发生变化，请检查结果');
  await plugin.connector('connector.writeback_ack',{plan_id:plan.id,text_hash:hash(written)});
  new Notice('结果已回写，个人笔记内容保留');return {status:'SYNCED',path:target};
 };
 class Workbench extends ItemView{
  constructor(leaf){super(leaf);this.inflight=false;this.lastAction=null;this.threadId=null;this.lastChat='';}
  getViewType(){return TYPE;}getDisplayText(){return 'Memo 研究';}getIcon(){return 'book-open-check';}
  async onOpen(){
   this.contentEl.empty();this.contentEl.addClass('memorive-workbench');this.contentEl.createEl('h3',{text:'Memo 研究'});
   const toolbar=this.contentEl.createDiv({cls:'memorive-toolbar'});button(toolbar,'刷新',()=>this.refresh());button(toolbar,'发送当前笔记',async()=>{const f=plugin.app.workspace.getActiveFile();if(!f)throw Error('请先打开笔记');await plugin.previewFiles([f]);});button(toolbar,'草稿与张力',()=>new ResearchModal(plugin.app,plugin,'').open());
   this.status=this.contentEl.createEl('p',{cls:'memorive-status'});this.threads=this.contentEl.createEl('select');this.threads.setAttribute('aria-label','Memo 会话');this.threads.onchange=()=>{this.threadId=this.threads.value||null;this.lastChat='';this.refresh().catch(e=>this.status.textContent=e.message);};
   this.question=this.contentEl.createEl('textarea',{attr:{placeholder:'在当前项目中检索或简短提问',rows:'3'}});
   const askbar=this.contentEl.createDiv({cls:'memorive-toolbar'});button(askbar,'检索证据',()=>this.search());button(askbar,'简短提问',async()=>{const a=await plugin.connector('connector.queue',{kind:'ask',question:this.question.value,thread_id:this.threadId,request_id:randomUUID()});this.lastAction=a.id;this.status.textContent='问题已提交；Memo 打开后使用已配置的问答模型';await this.refresh();});
   button(askbar,'交给 Agent',async()=>{const a=await plugin.connector('connector.queue',{kind:'agent',question:this.question.value,thread_id:this.threadId,request_id:randomUUID()});this.lastAction=a.id;this.status.textContent='已提交，结果将回到同一会话';await this.refresh();});
   this.results=this.contentEl.createDiv();this.chat=this.contentEl.createDiv();this.contentEl.createEl('h4',{text:'资料与结果'});this.docs=this.contentEl.createDiv();this.contentEl.createEl('h4',{text:'导入与任务'});this.jobs=this.contentEl.createDiv();await this.refresh();
   this.interval=window.setInterval(()=>this.refresh().catch(e=>this.status.textContent=e.message),2500);
  }
  async onClose(){if(this.interval)window.clearInterval(this.interval);}
  async search(){
   const out=await plugin.connector('connector.search',{query:this.question.value,limit:8});this.results.empty();
   for(const ref of out.results){const card=this.results.createDiv({cls:'memorive-source'});card.createEl('strong',{text:ref.title});card.createEl('p',{text:ref.text});card.createEl('small',{text:ref.page?'第 '+ref.page+' 页':'第 '+ref.line_start+'–'+ref.line_end+' 行'});
    button(card,'插入带出处的引用',async()=>{const r=await plugin.connector('connector.read',{evidence_id:ref.id,expected_hash:ref.content_hash});const editor=plugin.app.workspace.getActiveViewOfType(MarkdownView)?.editor;if(!editor)throw Error('请打开要插入的笔记');const link=r.source_uri||'file:///'+r.path.replace(/\\/g,'/').split('/').map(encodeURIComponent).join('/');editor.replaceSelection('\n> '+r.text.replace(/\n/g,'\n> ')+'\n\n[来源 · '+r.title.replace(/[\[\]]/g,'')+']('+link+') · '+r.id+' · '+r.content_hash+'\n');});
   }this.status.textContent=out.results.length+' 条证据';
  }
  async refresh(){
   if(this.inflight||!this.status)return;this.inflight=true;
   try{
    const state=await plugin.connector('connector.state');this.status.textContent=state.desktop_online?'Memo 已连接':'本地资料可检索；问答与主线处理等待 Memo 打开';
    const a=state.actions.find(a=>a.id===this.lastAction);if(a?.result?.thread_id){this.threadId=a.result.thread_id;this.lastAction=null;}if(a?.error)this.status.textContent=a.error;
    const threads=await plugin.connector('connector.chat');this.threads.empty();this.threads.createEl('option',{value:'',text:'新会话'});for(const t of threads.threads)this.threads.createEl('option',{value:t.id,text:t.title});this.threads.value=this.threadId||'';
    if(this.threadId){const thread=await plugin.connector('connector.chat',{thread_id:this.threadId});const key=String(thread.revision);if(key!==this.lastChat){this.chat.empty();for(const m of thread.messages.slice(-8)){const div=this.chat.createDiv({cls:'memorive-message'});div.createEl('strong',{text:m.role==='user'?'你':'Memo'});div.createEl('p',{text:m.text});if(m.usage)div.createEl('small',{text:'用时 '+(m.elapsed_ms||0)+' ms · '+(m.engine||'')});}this.lastChat=key;}}
    this.docs.empty();plugin.knownSources=new Set(state.documents.map(d=>d.path).filter(Boolean));
    for(const d of state.documents){const row=this.docs.createDiv({cls:'memorive-source'});row.createEl('strong',{text:d.title});row.createEl('small',{text:labels[d.state]||d.state});if(d.processing)row.createEl('p',{text:'主线：'+(d.processing.state||'')});
     button(row,'查看 / 回写结果',async()=>{const out=await plugin.connector('connector.outputs',{source_id:d.id}),m=modal(plugin,d.title),checks=[];for(const item of out.items){const label=m.contentEl.createEl('label',{cls:'memorive-choice'}),check=label.createEl('input',{type:'checkbox'});check.checked=true;label.createSpan({text:item.title+' · '+item.kind});m.contentEl.createEl('pre',{text:item.markdown});checks.push([check,item.id]);}button(m.contentEl,'回写到专属结果笔记',async()=>{const result=await plugin.writeResult(d.id,checks.filter(([c])=>c.checked).map(([,id])=>id));if(result.status==='SYNCED')m.close();});m.open();});
     if(d.path&&!d.processing)button(row,'交给主线处理',async()=>{await plugin.connector('connector.queue',{kind:'process',source_id:d.id,request_id:randomUUID()});new Notice('已提交主线，使用 Memo 中保存的模型与审核设置');await this.refresh();});
     if(d.processing?.state==='FAILED')button(row,'只重试失败步骤',()=>plugin.connector('connector.queue',{kind:'process_retry',source_id:d.id,request_id:randomUUID()}));
    }
    this.jobs.empty();for(const j of state.jobs){const row=this.jobs.createDiv({cls:'memorive-source'});row.createEl('p',{text:(labels[j.status]||j.status)+' · '+j.steps.filter(s=>s.status==='COMPLETE').length+'/'+j.steps.length+' · '+(j.elapsed_ms||0)+' ms'});for(const s of j.steps.filter(s=>s.error))row.createEl('small',{text:s.error});if(['ERROR','PARTIAL','INTERRUPTED'].includes(j.status))button(row,'只重试未完成资料',async()=>{await plugin.connector('connector.retry',{job_id:j.id});await this.refresh();});}
    for(const a of state.actions.filter(a=>['ERROR','INTERRUPTED','QUEUED'].includes(a.status))){const row=this.jobs.createDiv({cls:'memorive-source'});row.createEl('p',{text:(labels[a.status]||a.status)+' · '+a.kind});if(a.error)row.createEl('small',{text:a.error});if(a.status!=='QUEUED')button(row,'重试此请求',()=>plugin.connector('connector.action_retry',{action_id:a.id}));}
   }finally{this.inflight=false;}
  }
 }
 plugin.registerView(TYPE,leaf=>new Workbench(leaf));
 plugin.addCommand({id:'open-workbench',name:'打开 Memo 研究侧栏',callback:()=>plugin.openWorkbench().catch(e=>new Notice(e.message))});
 plugin.addCommand({id:'send-current-note',name:'将当前笔记送往 Memo（预览）',callback:()=>{const f=plugin.app.workspace.getActiveFile();if(f)plugin.previewFiles([f]).catch(e=>new Notice(e.message));}});
 plugin.registerEvent(plugin.app.workspace.on('file-menu',(menu,file)=>{if(file instanceof TFile&&['md','txt','pdf'].includes(file.extension))menu.addItem(item=>item.setTitle('送往 Memo（预览）').setIcon('book-open-check').onClick(()=>plugin.previewFiles([file]).catch(e=>new Notice(e.message))));}));
 let debounce;for(const event of ['modify','rename','delete'])plugin.registerEvent(plugin.app.vault.on(event,(file,oldPath)=>{const base=plugin.app.vault.adapter.getBasePath();if(plugin.knownSources?.has(path.join(base,oldPath||file.path))){clearTimeout(debounce);debounce=setTimeout(()=>plugin.connector('connector.refresh').catch(()=>{}),500);}}));
 plugin.register(()=>clearTimeout(debounce));
 return {TYPE};
}
module.exports={install,hash};
